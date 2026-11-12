#!/bin/sh
set -eu

case "${1:-}" in
    development) port=8081 ;;
    production) port=8082 ;;
    *) echo "Choose development or production" >&2; exit 2 ;;
esac

: "${RELEASE_IMAGE:?Missing verified image digest}"
: "${SETTLEMENT_API_TOKEN:?Configure an environment-scoped GitLab secret}"
case "$RELEASE_IMAGE" in
    "$CI_REGISTRY_IMAGE"@sha256:*) ;;
    *) echo "Expected a digest from the project registry" >&2; exit 1 ;;
esac

name="capstone-$1"
volume="$name-data"
candidate="$name-check-$CI_JOB_ID"
backup="$name-backup-$CI_JOB_ID"
docker volume inspect "$volume" >/dev/null
docker pull "$RELEASE_IMAGE"

cleanup_candidate() {
    docker rm -f "$candidate" >/dev/null 2>&1 || true
}

start_app() {
    app_name="$1"
    shift
    docker run -d --name "$app_name" --restart unless-stopped \
        -e SETTLEMENT_API_TOKEN --mount "type=volume,source=$volume,target=/data" \
        "$@" "$RELEASE_IMAGE"
}

trap cleanup_candidate EXIT
start_app "$candidate"
docker exec -i "$candidate" python - < scripts/smoke_ci.py
cleanup_candidate

if docker container inspect "$name" >/dev/null 2>&1; then
    docker rename "$name" "$backup"
    if ! docker stop "$backup"; then
        docker rename "$backup" "$name"
        exit 1
    fi
fi

if start_app "$name" -p "127.0.0.1:$port:8000" &&
    docker exec -i "$name" python - < scripts/smoke_ci.py; then
    if docker container inspect "$backup" >/dev/null 2>&1; then
        docker rm "$backup"
    fi
else
    docker rm -f "$name" >/dev/null 2>&1 || true
    if docker container inspect "$backup" >/dev/null 2>&1; then
        docker rename "$backup" "$name"
        docker start "$name"
    fi
    exit 1
fi
