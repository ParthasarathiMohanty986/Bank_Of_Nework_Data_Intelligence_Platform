import os
import sqlite3
from pathlib import Path


def database_path():
    return Path(os.environ.get("SETTLEMENT_DB", "var/settlement.db"))


def connect(path, *, initialize=False):
    path = Path(path)
    if initialize:
        path.parent.mkdir(parents=True, exist_ok=True)
    elif not path.is_file():
        raise FileNotFoundError("Run the settlement ingestion command first")
    db = sqlite3.connect(path, timeout=30, check_same_thread=False)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    if initialize:
        db.execute("PRAGMA journal_mode=WAL")
        db.executescript(Path(__file__).with_name("schema.sql").read_text())
    return db

