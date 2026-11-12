# Blue-green cutover evidence

On 12 November 2026, the local settlement dashboard switched from Blue V1 (Jenkins build #2, `capstone-settlement:jenkins-2`) to Green V2 (Jenkins build #3, `capstone-settlement:jenkins-3`). Green was built from commit `53c1ddf`.

Before the switch, the shared URL `http://127.0.0.1:8080/` routed to `blue-v1`. Green passed its direct smoke test on port 8083 and its results matched Blue. The Nginx configuration check passed and Nginx accepted the reload. After the switch, the same shared URL showed Version 2, and the API responses carried the `green-v2` deployment header.

| Check through the shared URL after cutover | HTTP status | Observed time |
| --- | --- | --- |
| `/health` | 200 | 83 ms |
| `/api/v1/settlement-summary` | 200 | 338 ms |
| `/api/v1/merchant-exceptions` | 200 | 161 ms |

All three checks met the 500 ms target in this smoke test. The settlement summary and merchant exception count matched the Blue baseline for 2026-01-01 through 2026-10-01.

| Check | Blue V1 and Green V2 |
| --- | --- |
| Health | `{"status":"ok"}` |
| Transaction count | 31,763 |
| Transaction amount | 136,675,776.64 |
| Settled amount | 129,429,359.85 |
| Settlement gap | 7,246,416.79 |
| Settlement rate | 94.70% |
| SLA rate | 82.03% |
| Merchant risk count | 16 |
| Excess settled amount | 0.00 |
| Merchant exception count | 61 |

The transaction amount minus the settled amount equals the reported gap. Blue remained healthy after the switch and later served the shared URL during the [incident rollback drill](blue-green-incident.md).
