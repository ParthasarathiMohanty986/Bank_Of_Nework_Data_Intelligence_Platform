import argparse
import csv
import hashlib
import io
import json
import re
from contextlib import closing
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from time import monotonic

from .db import connect, database_path


FIELDS = {
    "merchant": "merchant_id merchant_name merchant_category country risk_level effective_from effective_to",
    "transactions": "transaction_id merchant_id customer_id transaction_ts amount currency status payment_channel",
    "settlements": "settlement_id transaction_id settlement_ts settlement_amount settlement_status settlement_batch",
    "payment_events": "event_id transaction_id event_type event_ts ingestion_ts processing_ms",
}


class RowIssue(Exception):
    def __init__(self, reason, disposition="reject", parent=None):
        self.reason = reason
        self.disposition = disposition
        self.parent = parent


def required(value):
    if not value:
        raise RowIssue("missing_required_field")
    return value


def choice(value, options):
    if value not in options.split():
        raise RowIssue("invalid_enum")
    return value


def money(value, *, positive=False):
    number = Decimal(value)
    if not number.is_finite() or not 0 <= number <= Decimal("100000000000"):
        raise RowIssue("invalid_amount")
    paise = number * 100
    if paise != int(paise) or (positive and not paise):
        raise RowIssue("invalid_amount")
    return int(paise)


def timestamp(value):
    return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").isoformat(sep=" ")


def flag(db, raw, kind, reason):
    db.execute("INSERT OR IGNORE INTO finding(raw_id,disposition,reason) VALUES (?,?,?)",
               (raw, kind, reason))


def ingest_merchant(db, raw, row, affected):
    mid = required(row["merchant_id"])
    start = date.fromisoformat(row["effective_from"]).isoformat()
    end = date.fromisoformat(row["effective_to"]).isoformat() if row["effective_to"] else None
    if end and end < start:
        raise RowIssue("invalid_effective_period")
    values = (mid, start, end, required(row["merchant_name"]), required(row["merchant_category"]),
              required(row["country"]), choice(row["risk_level"], "LOW MEDIUM HIGH"))
    overlaps = db.execute(
        """SELECT * FROM merchant WHERE merchant_id=? AND effective_from<=COALESCE(?,'9999-12-31')
           AND COALESCE(effective_to,'9999-12-31')>=?""", (mid, end, start)
    ).fetchall()
    if overlaps:
        if len(overlaps) == 1 and tuple(overlaps[0]) == values:
            return
        # A snapshot may close a previously open version before adding a new one.
        prior = overlaps[0]
        if (len(overlaps) == 1 and prior["effective_from"] == start
                and prior["effective_to"] is None and end and tuple(prior)[3:] == values[3:]):
            # Preserve the validity of already accepted facts.
            referenced = db.execute(
                "SELECT 1 FROM payment WHERE merchant_id=? AND merchant_from=? AND substr(ts,1,10)>?",
                (mid, start, end),
            ).fetchone()
            if not referenced:
                db.execute("UPDATE merchant SET effective_to=? WHERE merchant_id=? AND effective_from=?",
                           (end, mid, start))
                return
        raise RowIssue("overlapping_merchant_history", "quarantine")
    db.execute("INSERT INTO merchant VALUES (?,?,?,?,?,?,?)", values)
    retry_children(db, "merchant:" + mid, affected)


def insert_fact(db, table, values, raw):
    # Allow only internal table names and bind all CSV values.
    if table not in ('payment', 'settlement', 'event'):
        raise RuntimeError('Unknown fact table')
    old = db.execute(f"SELECT * FROM {table} WHERE id=?", (values[0],)).fetchone()  # nosec B608
    if old:
        if tuple(old)[:-1] == values:
            if table == "event":
                flag(db, raw, "warning", "duplicate_event")
            return False
        if (table == "settlement" and old["status"] == "PENDING"
                and values[4] in ("SETTLED", "FAILED") and old["transaction_id"] == values[1]):
            db.execute(
                "UPDATE settlement SET ts=?,amount=?,status=?,batch_code=?,raw_id=? WHERE id=?",
                (*values[2:], raw, values[0]),
            )
            return True
        raise RowIssue("conflicting_id", "quarantine")
    marks = ",".join("?" for _ in (*values, raw))
    db.execute(f"INSERT INTO {table} VALUES ({marks})", (*values, raw))  # nosec B608
    return True


def process(db, source, raw, row, affected):
    if None in row or any(value is None for value in row.values()):
        raise RowIssue("malformed_csv_row")
    if source == "merchant":
        ingest_merchant(db, raw, row, affected)
        return
    tid = required(row["transaction_id"])
    if source == "transactions":
        ts = timestamp(row["transaction_ts"])
        mid = row["merchant_id"]
        amount = money(row["amount"], positive=True)
        if row["currency"] != "INR":
            raise RowIssue("unsupported_currency")
        status = choice(row["status"], "SUCCESS FAILED REVERSED")
        channel = choice(row["payment_channel"], "POS ONLINE QR")
        if not mid:
            raise RowIssue("missing_merchant", "quarantine")
        version = db.execute(
            """SELECT effective_from FROM merchant WHERE merchant_id=? AND effective_from<=?
               AND (effective_to IS NULL OR effective_to>=?)""", (mid, ts[:10], ts[:10])
        ).fetchone()
        if not version:
            raise RowIssue("merchant_version_not_found", "quarantine", "merchant:" + mid)
        if insert_fact(db, "payment", (tid, mid, version[0], ts, amount, status, channel), raw):
            affected.add(tid)
            retry_children(db, "payment:" + tid, affected)
        return

    if source == "settlements":
        sid = required(row["settlement_id"])
        status = choice(row["settlement_status"], "SETTLED PENDING FAILED")
        ts = timestamp(row["settlement_ts"]) if row["settlement_ts"] or status == "SETTLED" else None
        values = (sid, tid, ts, money(row["settlement_amount"]), status, required(row["settlement_batch"]))
    else:
        eid = required(row["event_id"])
        ts = timestamp(row["event_ts"])
        received = timestamp(row["ingestion_ts"])
        elapsed = (datetime.fromisoformat(received) - datetime.fromisoformat(ts)).total_seconds()
        duration = int(row["processing_ms"])
        if elapsed < 0 or not 0 <= duration <= 2**63 - 1:
            raise RowIssue("invalid_event_timing")
        values = (eid, tid, choice(row["event_type"], "CREATED AUTHORIZED SETTLED FAILED"),
                  ts, received, duration)

    parent = db.execute("SELECT ts FROM payment WHERE id=?", (tid,)).fetchone()
    if not parent:
        raise RowIssue("unmatched_transaction", "quarantine", "payment:" + tid)
    if source == "settlements":
        if ts and ts < parent[0]:
            raise RowIssue("settlement_before_payment")
        if insert_fact(db, "settlement", values, raw):
            affected.add(tid)
    elif insert_fact(db, "event", values, raw) and elapsed > 1800:
        flag(db, raw, "warning", "late_event")


def apply_row(db, source, raw, row, affected):
    try:
        process(db, source, raw, row, affected)
    except (ValueError, InvalidOperation):
        flag(db, raw, "reject", "invalid_field_format")
    except RowIssue as issue:
        flag(db, raw, issue.disposition, issue.reason)
        if issue.parent:
            db.execute("INSERT OR REPLACE INTO retry VALUES (?,?)", (raw, issue.parent))


def retry_children(db, parent, affected):
    rows = db.execute(
        "SELECT b.* FROM retry r JOIN bronze b ON b.id=r.raw_id WHERE r.parent=? ORDER BY b.id", (parent,)
    ).fetchall()
    for row in rows:
        db.execute("DELETE FROM retry WHERE raw_id=?", (row["id"],))
        db.execute("UPDATE finding SET resolved=1 WHERE raw_id=? AND disposition='quarantine'", (row["id"],))
        apply_row(db, row["source"], row["id"], json.loads(row["payload"]), affected)


def refresh_gold(db, tid):
    payment = db.execute(
        """SELECT p.*, m.name, m.risk FROM payment p JOIN merchant m
           ON m.merchant_id=p.merchant_id AND m.effective_from=p.merchant_from WHERE p.id=?""", (tid,)
    ).fetchone()
    if payment["status"] != "SUCCESS":
        return
    amount = payment["amount"]
    total, completion = 0, None
    for row in db.execute(
        "SELECT * FROM settlement WHERE transaction_id=? AND status='SETTLED' ORDER BY ts,id", (tid,)
    ):
        total += row["amount"]
        if completion is None and total >= amount:
            completion = row["ts"]
        if total > amount and row["amount"]:
            flag(db, row["raw_id"], "warning", "excess_settlement")
    on_time = bool(completion and
                   (datetime.fromisoformat(completion) - datetime.fromisoformat(payment["ts"])).total_seconds() <= 1800)
    db.execute("INSERT OR REPLACE INTO gold VALUES (?,?,?,?,?,?,?,?,?,?,?)",
               (tid, payment["ts"][:10], payment["ts"], payment["merchant_id"], payment["name"],
                payment["risk"], amount, total, min(amount, total), completion, int(on_time)))


def batch_files(folder):
    candidates = {}
    suffixes = set()
    for source in FIELDS:
        matches = [path for path in folder.glob("*.csv")
                   if re.fullmatch(rf"{source}(_\d{{4}}-\d{{2}}-\d{{2}})?\.csv", path.name)]
        if len(matches) != 1:
            raise ValueError(f"Expected one CSV for {source}")
        path = matches[0]
        suffixes.add(path.stem[len(source):])
        candidates[source] = path.read_bytes()
    if len(suffixes) != 1:
        raise ValueError("All four filenames must use the same batch date")
    return candidates


def ingest(folder, path):
    started = monotonic()
    files = batch_files(Path(folder))
    digest = hashlib.sha256(b"".join(hashlib.sha256(data).digest() for data in files.values())).hexdigest()
    affected = set()
    with closing(connect(path, initialize=True)) as db, db:
        db.execute("BEGIN IMMEDIATE")
        existing = db.execute("SELECT id FROM batch WHERE digest=?", (digest,)).fetchone()
        if existing:
            return {"batch": existing[0], "replayed": True, "affected_payments": 0}
        batch = db.execute("INSERT INTO batch(digest) VALUES (?)", (digest,)).lastrowid
        counts = {}
        for source, data in files.items():
            reader = csv.DictReader(io.StringIO(data.decode("utf-8-sig"), newline=""), strict=True)
            if reader.fieldnames != FIELDS[source].split():
                raise ValueError(f"Unexpected header for {source}")
            counts[source] = 0
            for line, row in enumerate(reader, 2):
                raw = db.execute("INSERT INTO bronze(batch_id,source,line,payload) VALUES (?,?,?,?)",
                                 (batch, source, line, json.dumps(row))).lastrowid
                apply_row(db, source, raw, row, affected)
                counts[source] += 1
        for tid in sorted(affected):
            refresh_gold(db, tid)
        findings = dict(db.execute(
            """SELECT disposition, count(*) FROM finding f JOIN bronze b ON b.id=f.raw_id
               WHERE b.batch_id=? AND f.resolved=0 GROUP BY disposition""", (batch,)
        ))
        return {"batch": batch, "replayed": False, "rows": counts, "findings": findings,
                "affected_payments": len(affected), "elapsed_seconds": round(monotonic() - started, 2)}


def main():
    parser = argparse.ArgumentParser(description="Load a merchant settlement CSV batch")
    parser.add_argument("folder", type=Path)
    parser.add_argument("--db", type=Path, default=database_path())
    args = parser.parse_args()
    try:
        print(json.dumps(ingest(args.folder, args.db), indent=2))
    except (ValueError, OSError, csv.Error) as exc:
        parser.exit(1, f"Ingestion failed: {exc}\n")


if __name__ == "__main__":
    main()

