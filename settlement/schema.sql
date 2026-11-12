CREATE TABLE IF NOT EXISTS batch (
 id INTEGER PRIMARY KEY, digest TEXT UNIQUE NOT NULL, loaded_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS bronze (
 id INTEGER PRIMARY KEY, batch_id INTEGER NOT NULL REFERENCES batch(id),
 source TEXT NOT NULL, line INTEGER NOT NULL, payload TEXT NOT NULL, UNIQUE(batch_id,source,line)
);
CREATE TABLE IF NOT EXISTS finding (
 raw_id INTEGER NOT NULL REFERENCES bronze(id), disposition TEXT NOT NULL
 CHECK(disposition IN ('reject','quarantine','warning')), reason TEXT NOT NULL,
 resolved INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(raw_id,reason)
);
CREATE TABLE IF NOT EXISTS retry (
 raw_id INTEGER PRIMARY KEY REFERENCES bronze(id), parent TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS retry_parent ON retry(parent);
CREATE TABLE IF NOT EXISTS merchant (
 merchant_id TEXT NOT NULL, effective_from TEXT NOT NULL, effective_to TEXT,
 name TEXT NOT NULL, category TEXT NOT NULL, country TEXT NOT NULL,
 risk TEXT NOT NULL CHECK(risk IN ('LOW','MEDIUM','HIGH')),
 CHECK(effective_to IS NULL OR effective_to >= effective_from),
 PRIMARY KEY(merchant_id,effective_from)
);
CREATE TABLE IF NOT EXISTS payment (
 id TEXT PRIMARY KEY NOT NULL, merchant_id TEXT NOT NULL, merchant_from TEXT NOT NULL,
 ts TEXT NOT NULL, amount INTEGER NOT NULL CHECK(amount > 0),
 status TEXT NOT NULL CHECK(status IN ('SUCCESS','FAILED','REVERSED')),
 channel TEXT NOT NULL CHECK(channel IN ('POS','ONLINE','QR')),
 raw_id INTEGER NOT NULL REFERENCES bronze(id),
 FOREIGN KEY(merchant_id,merchant_from) REFERENCES merchant(merchant_id,effective_from)
);
CREATE INDEX IF NOT EXISTS payment_merchant ON payment(merchant_id,ts);
CREATE TABLE IF NOT EXISTS settlement (
 id TEXT PRIMARY KEY NOT NULL, transaction_id TEXT NOT NULL REFERENCES payment(id),
 ts TEXT, amount INTEGER NOT NULL CHECK(amount >= 0),
 status TEXT NOT NULL CHECK(status IN ('SETTLED','PENDING','FAILED')),
 batch_code TEXT NOT NULL, raw_id INTEGER NOT NULL REFERENCES bronze(id),
 CHECK(status <> 'SETTLED' OR ts IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS settlement_payment ON settlement(transaction_id,ts,id);
CREATE TABLE IF NOT EXISTS event (
 id TEXT PRIMARY KEY NOT NULL, transaction_id TEXT NOT NULL REFERENCES payment(id),
 type TEXT NOT NULL CHECK(type IN ('CREATED','AUTHORIZED','SETTLED','FAILED')),
 ts TEXT NOT NULL, received TEXT NOT NULL, processing_ms INTEGER NOT NULL CHECK(processing_ms >= 0),
 raw_id INTEGER NOT NULL REFERENCES bronze(id)
);
CREATE INDEX IF NOT EXISTS event_payment ON event(transaction_id,ts,id);
CREATE TABLE IF NOT EXISTS gold (
 id TEXT PRIMARY KEY NOT NULL REFERENCES payment(id), day TEXT NOT NULL, ts TEXT NOT NULL,
 merchant_id TEXT NOT NULL, merchant_name TEXT NOT NULL, risk TEXT NOT NULL,
 amount INTEGER NOT NULL CHECK(amount > 0), actual INTEGER NOT NULL,
 credited INTEGER NOT NULL CHECK(credited BETWEEN 0 AND amount),
 completion TEXT, within_sla INTEGER NOT NULL CHECK(within_sla IN (0,1))
);
CREATE INDEX IF NOT EXISTS gold_day ON gold(day);
CREATE INDEX IF NOT EXISTS gold_merchant_day ON gold(merchant_id,day);

