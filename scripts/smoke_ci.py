"""Check a deployed container over HTTP and emit evidence without credentials."""

import json
import os
import time
from decimal import Decimal
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


def get(path, params=None):
    url = "http://127.0.0.1:8000" + path
    if params:
        url += "?" + urlencode(params)
    request = Request(url, headers={
        "Authorization": "Bearer " + os.environ["SETTLEMENT_API_TOKEN"],
    })
    with urlopen(request, timeout=3) as response:
        if response.status != 200:
            raise RuntimeError(f"{path} returned HTTP {response.status}")
        return json.load(response, parse_float=Decimal)


def main():
    for attempt in range(30):
        try:
            health = get("/health")
            break
        except (URLError, TimeoutError):
            if attempt == 29:
                raise
            time.sleep(1)
    if health != {"status": "ok"}:
        raise RuntimeError("Unexpected health response")

    status = get("/api/v1/data-status")
    scope = {key: status[key] for key in ("start_date", "end_date")}
    if not all(scope.values()) or not status["last_ingested_at_utc"]:
        raise RuntimeError("Deployment database has no ingested reporting data")
    summary = get("/api/v1/settlement-summary", scope)
    exceptions = get("/api/v1/merchant-exceptions", scope)
    paid = summary["transaction_amount"]
    settled = summary["settled_amount"]
    if not (summary["transaction_count"] > 0 and paid > 0 and 0 <= settled <= paid
            and paid - settled == summary["settlement_gap"]
            and abs(summary["settlement_rate"] - 100 * settled / paid) <= Decimal("0.005")
            and isinstance(exceptions, list)):
        raise RuntimeError("Settlement KPI reconciliation failed")
    print(json.dumps({"health": health, "scope": scope, "summary": summary,
                      "merchant_exception_count": len(exceptions)}, default=str, indent=2))


if __name__ == "__main__":
    main()
