"""Capture a local Blue V1 baseline and live HTTP latency evidence."""

import argparse
import hashlib
import json
import math
import os
import platform
import secrets
import socket
import sqlite3
import statistics
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

import httpx


def reconcile(db):
    dates = db.execute(
        "SELECT min(date(ts)), max(date(ts)) FROM payment WHERE status='SUCCESS'"
    ).fetchone()
    if not dates[0]:
        raise RuntimeError("Load the capstone dataset before running this check")
    merchants = db.execute("""
        WITH running AS (
            SELECT transaction_id, ts, amount,
                   sum(amount) OVER (PARTITION BY transaction_id ORDER BY ts,id) AS cumulative
            FROM settlement WHERE status='SETTLED'
        ), totals AS (
            SELECT r.transaction_id, sum(r.amount) AS actual,
                   min(CASE WHEN r.cumulative >= p.amount THEN r.ts END) AS completed_at
            FROM running r JOIN payment p ON p.id=r.transaction_id
            GROUP BY r.transaction_id
        )
        SELECT p.merchant_id, count(*) AS n, sum(p.amount) AS amount,
               sum(min(p.amount,coalesce(t.actual,0))) AS settled,
               sum(coalesce(t.actual,0)) AS actual, count(t.completed_at) AS completed,
               sum(CASE WHEN unixepoch(t.completed_at)-unixepoch(p.ts)<=1800
                        THEN 1 ELSE 0 END) AS on_time
        FROM payment p LEFT JOIN totals t ON p.id=t.transaction_id
        WHERE p.status='SUCCESS' GROUP BY p.merchant_id
    """).fetchall()
    totals = {key: sum(row[key] for row in merchants)
              for key in ("n", "amount", "settled", "actual", "completed", "on_time")}
    expected = {
        "transaction_count": totals["n"],
        "transaction_amount": totals["amount"] / 100,
        "settled_amount": totals["settled"] / 100,
        "settlement_gap": (totals["amount"] - totals["settled"]) / 100,
        "settlement_rate": round(100 * totals["settled"] / totals["amount"], 2),
        "sla_rate": round(100 * totals["on_time"] / totals["completed"], 2)
        if totals["completed"] else None,
        "excess_settled_amount": (totals["actual"] - totals["settled"]) / 100,
        "merchant_risk_count": sum(
            row["settled"] * 100 < row["amount"] * 95
            and row["on_time"] * 100 < row["completed"] * 90
            for row in merchants
        ),
    }
    exceptions = sorted(
        row["merchant_id"] for row in merchants
        if row["settled"] * 100 < row["amount"] * 95
        or row["on_time"] * 100 < row["completed"] * 90
    )
    return dict(zip(("start_date", "end_date"), dates)), expected, exceptions


def save(folder, name, content):
    (folder / name).write_text(json.dumps(content, indent=2) + "\n", encoding="utf-8")


def measure(client, endpoints, filters):
    def request(path):
        start = time.perf_counter()
        response = client.get(path, params=filters)
        elapsed = (time.perf_counter() - start) * 1000
        return {"endpoint": path, "status": response.status_code, "ms": elapsed}

    for endpoint in endpoints:
        client.get(endpoint, params=filters).raise_for_status()
    with ThreadPoolExecutor(max_workers=5) as pool:
        samples = list(pool.map(request, endpoints * 50))
    stats = {}
    for endpoint in endpoints:
        rows = [row for row in samples if row["endpoint"] == endpoint]
        times = sorted(row["ms"] for row in rows)
        stats[endpoint] = {
            "requests": len(times),
            "median_ms": round(statistics.median(times), 3),
            "p95_ms": round(times[math.ceil(.95 * len(times)) - 1], 3),
            "max_ms": round(max(times), 3),
            "at_or_above_500_ms": sum(value >= 500 for value in times),
            "non_200": sum(row["status"] != 200 for row in rows),
        }
    return stats, samples


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=Path("var/settlement.db"))
    parser.add_argument("--output", type=Path, default=Path("evidence/01_test_results"))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    db_uri = args.db.resolve().as_uri() + "?mode=ro"
    with closing(sqlite3.connect(db_uri, uri=True)) as db:
        db.row_factory = sqlite3.Row
        filters, expected, exceptions = reconcile(db)
        batch_digests = [row[0] for row in db.execute("SELECT digest FROM batch ORDER BY id")]
        fk_errors = len(db.execute("PRAGMA foreign_key_check").fetchall())
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    token = secrets.token_urlsafe(32)
    env = {**os.environ, "SETTLEMENT_API_TOKEN": token, "SETTLEMENT_DB": str(args.db.resolve())}
    server = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "settlement.api:app", "--host", "127.0.0.1",
         "--port", str(port), "--no-access-log"], env=env,
    )
    endpoints = ["/api/v1/settlement-summary", "/api/v1/merchant-exceptions"]
    try:
        with httpx.Client(base_url=f"http://127.0.0.1:{port}", trust_env=False, timeout=5,
                          headers={"Authorization": f"Bearer {token}"}) as client:
            for _ in range(100):
                if server.poll() is not None:
                    raise RuntimeError("Uvicorn exited before becoming ready")
                try:
                    health = client.get("/health")
                except httpx.ConnectError:
                    time.sleep(.1)
                    continue
                health.raise_for_status()
                break
            else:
                raise RuntimeError("Uvicorn did not become ready")
            summary, merchant_list = [client.get(path, params=filters) for path in endpoints]
            summary.raise_for_status()
            merchant_list.raise_for_status()
            actual = summary.json()
            actual_exceptions = sorted(row["merchant_id"] for row in merchant_list.json())
            passed = actual == expected and actual_exceptions == exceptions and fk_errors == 0
            metadata = {"captured_at_utc": datetime.now(timezone.utc).isoformat(),
                        "version": "Blue V1 local baseline", "scope": filters,
                        "api_source_sha256": hashlib.sha256(Path("settlement/api.py").read_bytes()).hexdigest(),
                        "batch_sha256": batch_digests}
            save(args.output, "kpi-baseline.json", {
                **metadata, "silver_expected": expected, "api_actual": actual,
                "expected_exception_ids": exceptions, "api_exception_ids": actual_exceptions,
                "foreign_key_errors": fk_errors, "health_status": health.status_code,
                "passed": passed,
            })
            stats, samples = measure(client, endpoints, filters)
            timing_passed = all(not row["at_or_above_500_ms"] and not row["non_200"]
                                for row in stats.values())
            save(args.output, "api-timing.json", {
                **metadata, "environment": platform.platform(), "python": platform.python_version(),
                "transport": "Local HTTP against one Uvicorn process", "concurrency": 5,
                "request_count": len(samples), "threshold_ms": 500, "warmup_per_endpoint": 1,
                "passed": timing_passed, "results": stats, "samples": samples,
            })
            print(json.dumps({"reconciled": passed, "metrics": actual, "timing": stats}, indent=2))
            return 0 if passed and timing_passed else 1
    finally:
        server.terminate()
        server.wait(timeout=10)


if __name__ == "__main__":
    raise SystemExit(main())
