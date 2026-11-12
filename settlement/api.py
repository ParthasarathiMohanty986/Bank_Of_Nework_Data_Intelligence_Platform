import os
import re
import secrets
import sqlite3
from contextlib import closing
from datetime import date, datetime
from pathlib import Path
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .db import connect, database_path


app = FastAPI(title="Merchant Settlement Intelligence", version="1.0.0")
static = Path(__file__).with_name("web")
app.mount("/assets", StaticFiles(directory=static), name="assets")
bearer = HTTPBearer(auto_error=False)


def authorize(credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]):
    expected = os.getenv("SETTLEMENT_API_TOKEN")
    if not expected:
        raise HTTPException(503, "API access is not configured")
    if not credentials or not secrets.compare_digest(credentials.credentials.encode(), expected.encode()):
        raise HTTPException(401, "A valid operations token is required",
                            headers={"WWW-Authenticate": "Bearer"})


def read_db():
    try:
        db = connect(database_path())
    except (OSError, sqlite3.Error):
        raise HTTPException(503, "Settlement database is unavailable") from None
    with closing(db), db:
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        yield db


Database = Annotated[sqlite3.Connection, Depends(read_db)]
access = [Depends(authorize)]
# Query fragments below are fixed SQL. Request values are always bound parameters.
MERCHANT_ID = r"^[A-Za-z0-9_-]+$"
AGG = """COUNT(*) AS n, COALESCE(SUM(amount),0) AS amount,
         COALESCE(SUM(credited),0) AS credited, COALESCE(SUM(actual),0) AS actual,
         COUNT(completion) AS completed, COALESCE(SUM(within_sla),0) AS on_time"""


def filters(
    db: Database,
    start_date: Annotated[str, Query()],
    end_date: Annotated[str, Query()],
    merchant_id: Annotated[str | None, Query(pattern=MERCHANT_ID, max_length=64)] = None,
):
    try:
        if not all(re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) for value in (start_date, end_date)):
            raise ValueError
        if date.fromisoformat(start_date) > date.fromisoformat(end_date):
            raise ValueError
    except ValueError:
        raise HTTPException(400, "Use valid YYYY-MM-DD dates with start_date <= end_date") from None
    sql, params = "day BETWEEN ? AND ?", (start_date, end_date)
    if merchant_id:
        if not db.execute("SELECT 1 FROM merchant WHERE merchant_id=?", (merchant_id,)).fetchone():
            raise HTTPException(404, "Unknown merchant")
        sql += " AND merchant_id=?"
        params += (merchant_id,)
    return sql, params


Filters = Annotated[tuple, Depends(filters)]


class Summary(BaseModel):
    transaction_count: int
    transaction_amount: float
    settled_amount: float
    settlement_gap: float
    settlement_rate: float | None = Field(ge=0, le=100)
    sla_rate: float | None = Field(ge=0, le=100)
    merchant_risk_count: int
    excess_settled_amount: float


class MerchantMetric(BaseModel):
    merchant_id: str
    merchant_name: str
    risk_level: str
    settlement_rate: float
    sla_rate: float | None
    settlement_gap: float


def percentage(numerator, denominator):
    return round(100 * numerator / denominator, 2) if denominator else None


def merchant_rows(db, scope):
    where, params = scope
    return db.execute(f"""
        WITH selected AS (
            SELECT *, ROW_NUMBER() OVER(PARTITION BY merchant_id ORDER BY ts DESC,id DESC) AS latest
            FROM gold WHERE {where}
        )
        SELECT merchant_id, MAX(CASE WHEN latest=1 THEN merchant_name END) AS name,
               MAX(CASE WHEN latest=1 THEN risk END) AS risk, SUM(amount-credited) AS gap, {AGG}
        FROM selected GROUP BY merchant_id ORDER BY gap DESC, merchant_id
    """, params).fetchall()  # nosec B608


def below_rate(row):
    return row["credited"] * 100 < row["amount"] * 95


def below_sla(row):
    return bool(row["completed"] and row["on_time"] * 100 < row["completed"] * 90)


def merchant_metric(row):
    return MerchantMetric(merchant_id=row["merchant_id"], merchant_name=row["name"],
                          risk_level=row["risk"], settlement_rate=percentage(row["credited"], row["amount"]),
                          sla_rate=percentage(row["on_time"], row["completed"]),
                          settlement_gap=(row["amount"] - row["credited"]) / 100)


@app.middleware("http")
async def response_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/", include_in_schema=False)
def dashboard():
    return FileResponse(static / "index.html")


@app.get("/health")
def health(db: Database):
    try:
        db.execute("SELECT id FROM gold LIMIT 1")
        db.execute("SELECT id FROM batch LIMIT 1")
    except sqlite3.Error:
        raise HTTPException(503, "Settlement schema is unavailable") from None
    return {"status": "ok"}


@app.get("/api/v1/data-status", dependencies=access)
def data_status(db: Database):
    data = db.execute("SELECT MIN(day) AS start_date, MAX(day) AS end_date FROM gold").fetchone()
    return {**dict(data), "last_ingested_at_utc": db.execute("SELECT MAX(loaded_at) FROM batch").fetchone()[0]}


@app.get("/api/v1/settlement-summary", dependencies=access, response_model=Summary)
def summary(db: Database, scope: Filters):
    where, params = scope
    row = db.execute(f"SELECT {AGG} FROM gold WHERE {where}", params).fetchone()  # nosec B608
    return Summary(
        transaction_count=row["n"], transaction_amount=row["amount"] / 100,
        settled_amount=row["credited"] / 100, settlement_gap=(row["amount"] - row["credited"]) / 100,
        settlement_rate=percentage(row["credited"], row["amount"]),
        sla_rate=percentage(row["on_time"], row["completed"]),
        merchant_risk_count=sum(below_rate(m) and below_sla(m) for m in merchant_rows(db, scope)),
        excess_settled_amount=(row["actual"] - row["credited"]) / 100,
    )


@app.get("/api/v1/daily-trend", dependencies=access)
def trend(db: Database, scope: Filters):
    where, params = scope
    return [
        {"date": r["day"], "transaction_amount": r["amount"] / 100, "settled_amount": r["credited"] / 100}
        for r in db.execute(f"SELECT day, {AGG} FROM gold WHERE {where} GROUP BY day ORDER BY day", params)  # nosec B608
    ]


@app.get("/api/v1/merchant-gaps", dependencies=access, response_model=list[MerchantMetric])
def merchant_gaps(db: Database, scope: Filters):
    return [merchant_metric(row) for row in merchant_rows(db, scope)[:10]
            if row["amount"] > row["credited"]]


@app.get("/api/v1/merchant-exceptions", dependencies=access, response_model=list[MerchantMetric])
def merchant_exceptions(db: Database, scope: Filters):
    return [merchant_metric(row) for row in merchant_rows(db, scope) if below_rate(row) or below_sla(row)]


@app.get("/api/v1/payment-exceptions", dependencies=access)
def payment_exceptions(db: Database, scope: Filters,
                       limit: Annotated[int, Query(ge=1, le=100)] = 25,
                       offset: Annotated[int, Query(ge=0, le=1000000)] = 0):
    where, params = scope
    now = datetime.now(ZoneInfo("Asia/Kolkata")).strftime("%Y-%m-%d %H:%M:%S")
    rows = db.execute(f"""
        SELECT id, merchant_id, ts, risk, amount, credited, completion,
               CASE WHEN completion IS NULL AND unixepoch(?) - unixepoch(ts) > 1800
                    THEN 1 ELSE 0 END AS overdue
        FROM gold WHERE {where} AND (credited < amount OR within_sla=0)
        ORDER BY amount-credited DESC, ts DESC,id LIMIT ? OFFSET ?
    """, (now, *params, limit, offset))  # nosec B608
    return [{
        "transaction_id": row["id"], "merchant_id": row["merchant_id"],
        "transaction_ts": row["ts"], "risk_level": row["risk"],
        "transaction_amount": row["amount"] / 100, "settled_amount": row["credited"] / 100,
        "settlement_gap": (row["amount"] - row["credited"]) / 100,
        "status": ("SETTLED_LATE" if row["completion"] else "PARTIAL" if row["credited"] else "UNSETTLED"),
        "overdue": bool(row["overdue"]),
    } for row in rows]

