# Merchant Settlement Intelligence

A full-stack capstone for Payments Operations. The ingestion command loads CSVs into Bronze, validates them into Silver, and refreshes affected Gold payment records. FastAPI serves the HTML/JavaScript dashboard from that database.

## Run locally

From this folder in PowerShell:

```powershell
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements-dev.txt
$data = 'C:/Users/PARTHA/OneDrive/Desktop/capstone2_datasets'
.venv/Scripts/python.exe -m settlement.ingest $data
.venv/Scripts/python.exe -m settlement.ingest "$data/incremental"
.\start.ps1
```

Open http://127.0.0.1:8000 and enter the token printed in your terminal. The initial dashboard spans all loaded payment dates. Use /docs for the API contract. The token is kept in memory by the dashboard. The default database is var/settlement.db. Set SETTLEMENT_DB to use a different location for both commands.

A batch folder contains all four CSVs with base names or a shared date suffix. Submit the incremental folder again to verify replay reports zero affected payments. A new batch can be loaded while the app is running. Apply the dashboard filters again to fetch updated results.

Run the isolated automated tests with `.venv/Scripts/python.exe -m pytest -q`. See [business specification](docs/business.md), [technical specification](docs/technical.md), [acceptance scenarios](tests/acceptance.feature), and [deployment design](docs/deployment.md).
