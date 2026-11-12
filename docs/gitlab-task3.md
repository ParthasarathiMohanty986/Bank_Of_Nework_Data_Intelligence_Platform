# Day 2 Task 3 GitLab pipeline

The pipeline runs on a dedicated GitLab project runner backed by Docker Desktop's Linux engine. A push to the protected default branch starts validation, pytest, Bandit, Gitleaks and pip-audit. The build pushes a uniquely tagged candidate to the project registry. Trivy scans the candidate by digest. The package job releases that digest to the deployment jobs only if the scan completes with zero critical findings. GitLab keeps the test, scan and DEV smoke reports as job artifacts.

## Set up the project runner

1. In GitLab, protect the default branch and enable the project's Container Registry. Under **Settings → CI/CD → Runners**, create a project runner with tag `capstone-docker`. Mark it **Protected** and keep it dedicated to this project. The Docker socket gives its jobs control over Docker Desktop, so restrict writes to the protected branch to trusted maintainers.
2. With Docker Desktop running in Linux mode, start a runner container in PowerShell. Use the registration command shown by GitLab with its authentication token. Keep that token out of source control.

```powershell
docker volume create capstone-runner-config
docker run -d --name capstone-runner --restart unless-stopped -v capstone-runner-config:/etc/gitlab-runner -v /var/run/docker.sock:/var/run/docker.sock gitlab/gitlab-runner:alpine
docker exec -it capstone-runner gitlab-runner register --url https://gitlab.com --executor docker --docker-image docker:29-cli --docker-volumes /var/run/docker.sock:/var/run/docker.sock
```

When prompted, provide the runner authentication token from GitLab. Confirm that the runner is online in the project before pushing to the default branch.

3. In **Settings → CI/CD → Variables**, add a masked, protected `SETTLEMENT_API_TOKEN` scoped to `development`. Add a separate value for the `production` scope before using the manual job. Use GitLab's built-in registry variables for registry access. If protected environments are available in your GitLab tier, restrict who may deploy to `production`.

## Prepare the DEV database

The deployment expects a preloaded SQLite database at `/data/settlement.db` in a volume called `capstone-development-data`. Seed it once with approved demo data. The following assumes the Day 1 database exists locally and Docker has the Day 2 app image.

```powershell
.venv/Scripts/python.exe -c "import sqlite3; a=sqlite3.connect('file:var/settlement.db?mode=ro',uri=True); b=sqlite3.connect('var/ci-seed.db'); a.backup(b); b.execute('PRAGMA journal_mode=DELETE'); b.close(); a.close()"
$seed = (Resolve-Path var/ci-seed.db).Path
docker volume create capstone-development-data
docker run --rm --user 0 --entrypoint sh --mount "type=bind,source=$seed,target=/seed.db,readonly" --mount "type=volume,source=capstone-development-data,target=/data" capstone-settlement:day2-security -ec 'test ! -e /data/settlement.db; cp /seed.db /data/settlement.db; chown 10001 /data/settlement.db'
```

DEV is available on `http://127.0.0.1:8081` on the Docker host. Its smoke check validates health, data availability, the summary, and the merchant exceptions response. The manual production job expects its own approved data volume, `capstone-production-data`, writable by the application's UID 10001. It preflights a candidate before replacing the container on port 8082 and restores the previous container if the replacement fails its smoke check. Traffic routing for the full blue-green exercise belongs to Task 5.

For Task 3 evidence, capture the pipeline page, the artifact reports, and the manual production job waiting for approval. Click that job only when the intended production or capstone demo environment is ready.
