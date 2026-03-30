#!/usr/bin/env bash
set -euo pipefail

CONTAINER_NAME="${1:-vrooem-gateway-gateway-1}"

resolve_api_key() {
    if [[ -n "${VROOEM_GATEWAY_API_KEY:-}" ]]; then
        printf '%s\n' "${VROOEM_GATEWAY_API_KEY}"
        return
    fi

    docker exec "${CONTAINER_NAME}" python -c "import os; print((os.getenv('GATEWAY_API_KEYS', '').split(',')[0] or '').strip())"
}

echo "[1/3] Refreshing unified locations inside ${CONTAINER_NAME}"
docker exec "${CONTAINER_NAME}" python -m app.scripts.refresh_locations_json

echo "[2/3] Restarting ${CONTAINER_NAME} so runtime state reloads the refreshed file"
docker restart "${CONTAINER_NAME}" >/dev/null

echo "[3/3] Verifying loaded location metadata from the running gateway"
API_KEY="$(resolve_api_key)"

if [[ -z "${API_KEY}" ]]; then
    echo "Unable to resolve X-API-Key for ${CONTAINER_NAME}" >&2
    exit 1
fi

docker exec "${CONTAINER_NAME}" python -c "import json,time,urllib.request; req=urllib.request.Request('http://127.0.0.1:8000/api/v1/locations/status', headers={'x-api-key':'${API_KEY}'}); last_error=None
for _ in range(30):
    try:
        print(urllib.request.urlopen(req).read().decode())
        break
    except Exception as exc:
        last_error=exc
        time.sleep(1)
else:
    raise last_error"
