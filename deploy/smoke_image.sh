#!/usr/bin/env bash
set -euo pipefail
image="${1:?usage: smoke_image.sh IMAGE}"
# Register cleanup only after docker run succeeds: never remove a pre-existing container.
cid="$(docker run --detach --network none --read-only \
  --tmpfs /tmp:rw,noexec,nosuid,size=33554432 --env PORT=9091 "$image" --smoke)"
trap 'docker rm --force "$cid" >/dev/null 2>&1 || true' EXIT
ready=false
for attempt in {1..60}; do
  if docker exec "$cid" python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:9091/health',timeout=1).read()" >/dev/null 2>&1; then
    ready=true; break
  fi
  sleep 0.2
done
if [[ "$ready" != true ]]; then docker logs "$cid"; exit 1; fi
docker exec -i "$cid" python - <<'PY'
import importlib.metadata, json, os, tempfile, urllib.error, urllib.request
from pathlib import Path
assert os.getuid() == 10001
base = 'http://127.0.0.1:9091'
for path in ('/health', '/', '/static/app.js', '/static/admin.js', '/static/styles.css'):
    with urllib.request.urlopen(base + path, timeout=3) as response:
        assert response.status == 200 and response.read()
for path in ('/questions', '/investigations'):
    request = urllib.request.Request(base + path, data=b'{"question":"smoke"}', headers={'Content-Type':'application/json'})
    try:
        urllib.request.urlopen(request, timeout=3)
        raise AssertionError('Providers must stay disabled')
    except urllib.error.HTTPError as error:
        assert error.code == 503
try:
    urllib.request.urlopen(base + '/documents', timeout=3)
    raise AssertionError('Management must stay disabled')
except urllib.error.HTTPError as error:
    assert error.code == 503
with tempfile.TemporaryDirectory() as folder:
    Path(folder, 'upload-check').write_text('ephemeral')
assert all(not Path('/opt/app', name).exists() for name in ('.env', 'tests', 'data', 'reports', 'src'))
assert all(name not in os.environ for name in ('DB_PASSWORD', 'GEMINI_API_KEY', 'ADMIN_API_TOKEN'))
installed = {dist.metadata['Name'].lower() for dist in importlib.metadata.distributions()}
assert not installed.intersection({'pytest', 'pytest-asyncio', 'pytest-cov', 'testcontainers', 'docker'})
print(json.dumps({'health':'ok','assets':'ok','port':9091,'uid':os.getuid(),'providers':'disabled','temp_uploads':'ok','dev_dependencies':False}))
PY
docker stop --time 10 "$cid" >/dev/null
[[ "$(docker inspect --format '{{.State.ExitCode}}' "$cid")" == 0 ]]
echo 'Graceful SIGTERM shutdown: exit 0'
