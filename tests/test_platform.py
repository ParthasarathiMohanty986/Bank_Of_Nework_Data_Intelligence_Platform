import csv
from contextlib import closing
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from settlement.api import app
from settlement.db import connect
from settlement.ingest import FIELDS, ingest


def write_batch(folder, **data):
    folder.mkdir()
    for source, fields in FIELDS.items():
        with (folder / f"{source}.csv").open("w", newline="", encoding="utf-8") as out:
            writer = csv.writer(out)
            writer.writerow(fields.split())
            writer.writerows(data.get(source, []))
    return folder


def payment(tid, merchant="M1", status="SUCCESS", ts="2026-02-01 10:00:00", amount="10000"):
    return (tid, merchant, "private-customer", ts, amount, "INR", status, "QR")


def settlement(sid, tid, amount, minute="30", status="SETTLED"):
    return (sid, tid, "2026-02-01 10:" + minute + ":00", amount, status, "B1")


@pytest.fixture
def dataset(tmp_path):
    event = ("E1", "T1", "CREATED", "2026-02-01 10:00:00", "2026-02-01 10:45:00", 2)
    base = write_batch(
        tmp_path / "base",
        merchant=[
            ("M1", "Test Retail", "Retail", "IN", "LOW", "2026-01-01", "2026-03-31"),
            ("M1", "Test Retail", "Retail", "IN", "HIGH", "2026-04-01", ""),
        ],
        transactions=[payment("T1"), payment("T2", status="FAILED"), payment("T3", merchant=""),
                      payment("T4", ts="2026-04-01 10:00:00"), payment("T5")],
        settlements=[settlement("S1", "T1", "8000", "10"), settlement("S2", "T1", "-3"),
                     settlement("S3", "T6", "10000"), settlement("S4", "T2", "10000")],
        payment_events=[("E2", "T1", "AUTHORIZED", "2026-02-01 10:00:02", "2026-02-01 10:00:04", 2),
                        event, event],
    )
    path = tmp_path / "test.db"
    ingest(base, path)
    return path


def test_silver_grain_quality_and_risk(dataset):
    with closing(connect(dataset)) as db:
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
        assert db.execute("SELECT id FROM payment GROUP BY id HAVING COUNT(*)>1").fetchall() == []
        assert [tuple(r) for r in db.execute("SELECT id,risk FROM gold ORDER BY id")] == [
            ("T1", "LOW"), ("T4", "HIGH"), ("T5", "LOW"),
        ]
        assert [r[0] for r in db.execute("SELECT id FROM event WHERE transaction_id='T1' ORDER BY ts,id")] == [
            "E1", "E2",
        ]
        assert {tuple(r) for r in db.execute("SELECT disposition,reason FROM finding")} == {
            ("warning", "late_event"), ("warning", "duplicate_event"),
            ("reject", "invalid_amount"), ("quarantine", "missing_merchant"),
            ("quarantine", "unmatched_transaction"),
        }


def test_incremental_split_orphan_repair_and_replay(dataset, tmp_path, client):
    with closing(connect(dataset)) as db:
        assert tuple(db.execute("SELECT amount,credited,completion FROM gold WHERE id='T1'").fetchone()) == (
            1000000, 800000, None,
        )
    new = write_batch(tmp_path / "next", transactions=[payment("T6")],
                      settlements=[settlement("S5", "T1", "2000")])
    assert ingest(new, dataset)["affected_payments"] == 2
    assert ingest(new, dataset)["affected_payments"] == 0
    with closing(connect(dataset)) as db:
        assert tuple(db.execute("SELECT amount,credited,completion,within_sla FROM gold WHERE id='T1'").fetchone()) == (
            1000000, 1000000, "2026-02-01 10:30:00", 1,
        )
        assert db.execute("SELECT credited FROM gold WHERE id='T6'").fetchone()[0] == 1000000
        assert db.execute("SELECT resolved FROM finding WHERE reason='unmatched_transaction'").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM batch").fetchone()[0] == 2
    response = client.get("/api/v1/settlement-summary", params=scope)
    assert response.status_code == 200
    summary = response.json()
    # T1 has two settlements, T6 has one, and T5 is outstanding.
    assert summary["transaction_count"] == 3
    assert summary["transaction_amount"] == 30000
    assert summary["settled_amount"] == 20000
    assert summary["settlement_gap"] == 10000
    assert summary["settlement_rate"] == 66.67


def test_failed_batch_is_atomic(dataset, tmp_path):
    new = write_batch(tmp_path / "bad", transactions=[payment("NEW")])
    (new / "settlements.csv").write_text("incorrect_header\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Unexpected header"):
        ingest(new, dataset)
    with closing(connect(dataset)) as db:
        assert db.execute("SELECT COUNT(*) FROM batch").fetchone()[0] == 1
        assert db.execute("SELECT id FROM payment WHERE id='NEW'").fetchone() is None


@pytest.fixture
def client(dataset, monkeypatch):
    monkeypatch.setenv("SETTLEMENT_DB", str(dataset))
    monkeypatch.setenv("SETTLEMENT_API_TOKEN", "test-fixture")
    return TestClient(app, headers={"Authorization": "Bearer test-fixture"})


scope = {"start_date": "2026-02-01", "end_date": "2026-02-28"}


def test_api_contract_and_dashboard(client):
    endpoint = "/api/v1/settlement-summary"
    response = client.get(endpoint, params=scope)
    assert response.status_code == 200
    assert response.json() == {
        "transaction_count": 2, "transaction_amount": 20000, "settled_amount": 8000,
        "settlement_gap": 12000, "settlement_rate": 40, "sla_rate": None,
        "merchant_risk_count": 0, "excess_settled_amount": 0,
    }
    assert client.get(endpoint, params={**scope, "start_date": "2026-02-30"}).status_code == 400
    assert client.get(endpoint, params={**scope, "merchant_id": "UNKNOWN"}).status_code == 404
    assert client.get(endpoint, params={**scope, "merchant_id": "' OR 1=1"}).status_code == 422
    assert client.get(endpoint).status_code == 422
    assert client.get(endpoint, params=scope, headers={"Authorization": "Bearer bad"}).status_code == 401
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/").status_code == 200
    assert client.get("/assets/dashboard.js").status_code == 200
    assert client.get("/openapi.json").status_code == 200
    assert client.get("/api/v1/merchant-exceptions", params=scope).json()[0]["merchant_id"] == "M1"
    assert client.get("/api/v1/merchant-gaps", params=scope).json()[0]["settlement_gap"] == 12000
    assert client.get("/api/v1/daily-trend", params=scope).json() == [
        {"date": "2026-02-01", "transaction_amount": 20000, "settled_amount": 8000},
    ]
    payments = client.get("/api/v1/payment-exceptions", params=scope)
    assert [(row["transaction_id"], row["status"]) for row in payments.json()] == [
        ("T5", "UNSETTLED"), ("T1", "PARTIAL"),
    ]
    assert "private-customer" not in payments.text


def test_over_settlement_capped_and_late_sla(client, dataset, tmp_path):
    new = write_batch(tmp_path / "excess", settlements=[
        settlement("S6", "T1", "3000", "31"), settlement("S7", "T5", "10000", "31"),
    ])
    ingest(new, dataset)
    result = client.get("/api/v1/settlement-summary", params=scope).json()
    assert result["settlement_rate"] == 100
    assert result["settlement_gap"] == 0
    assert result["excess_settled_amount"] == 1000
    assert result["sla_rate"] == 0
    assert result["merchant_risk_count"] == 0
    assert len(client.get("/api/v1/merchant-exceptions", params=scope).json()) == 1



def test_merchant_gap_ranks_aggregates(client, dataset, tmp_path):
    batch = write_batch(
        tmp_path / "new-merchants",
        merchant=[
            ("M2", "Small gaps", "Retail", "IN", "LOW", "2026-01-01", ""),
            ("M3", "Large gap", "Retail", "IN", "HIGH", "2026-01-01", ""),
        ],
        transactions=[
            payment("T60", merchant="M2", amount="600"),
            payment("T7", merchant="M2", amount="600"),
            payment("T8", merchant="M3", amount="1000"),
            payment("T9", merchant="M3", amount="300"),
        ],
        settlements=[settlement("S6", "T9", "300", "31")],
    )
    ingest(batch, dataset)
    gaps = client.get("/api/v1/merchant-gaps", params=scope).json()
    assert [(row["merchant_id"], row["settlement_gap"]) for row in gaps] == [
        ("M1", 12000), ("M2", 1200), ("M3", 1000),
    ]
    assert client.get("/api/v1/settlement-summary", params=scope).json()["merchant_risk_count"] == 1
