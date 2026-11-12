# Green settlement incident and rollback drill

On 12 November 2026, Green V2 was serving the local dashboard on port 8080. Its baseline for 1 January through 1 October was a 94.70% settlement rate and zero excess settlements.

We backed up Green's SQLite database and simulated duplicate settlements by doubling the actual settled amount for three fully settled payments across three merchants in Green's reporting table. The app and Jenkins image stayed the same. The API reported 560,042.46 in excess settlements and continued returning HTTP 200. The rate stayed at 94.70% because it uses credited amounts capped at the original payment amount.

We routed the shared URL back to Blue V1 at about 17:58 IST. The first probe ran before the Nginx reload had taken effect. The subsequent probe confirmed the `blue-v1` response header, HTTP 200, and the original settlement summary with zero excess. We captured Green's faulty response through its separate preview URL after rollback, restored the three modified amounts from backup, and verified that Green's preview returned to the same baseline.

The rollback restored trusted reporting while Green was investigated. A check on excess settlement amounts would catch this scenario even when health and settlement rate pass. For a future deployment gate, compare excess amounts with the approved baseline and flag unexpected increases.

[API observations and affected counts](blue-green-incident.json)
