FROM node:24.19.0-bookworm-slim AS qualification
# Reuse the exact runtime already delivered in the verified Windows artifact.
# Only the operator pre-deploy probe runs it; public requests cannot start it.
RUN apt-get update && apt-get install -y --no-install-recommends python3 ca-certificates && rm -rf /var/lib/apt/lists/*
RUN python3 - <<'PY'
import hashlib, pathlib, urllib.request, zipfile
archive = pathlib.Path('/tmp/desktop.zip')
url = 'https://velia-desktop-windows-build-production.up.railway.app/VELIA-Desktop-0.2.0-win-x64.zip'
urllib.request.urlretrieve(url, archive)
assert hashlib.file_digest(archive.open('rb'), 'sha256').hexdigest() == '54b6ed86541bcd7ce9e874d5d997630c51c905f43f751eb8b88c3db2fdc50bf9'
root = pathlib.Path('/opt/velia-qualification/harness')
root.mkdir(parents=True)
count = 0
with zipfile.ZipFile(archive) as bundle:
    for entry in bundle.infolist():
        marker = 'resources/harness/'
        if marker not in entry.filename or entry.is_dir():
            continue
        relative = pathlib.PurePosixPath(entry.filename.split(marker, 1)[1])
        assert not relative.is_absolute() and '..' not in relative.parts
        target = root.joinpath(*relative.parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(bundle.read(entry))
        count += 1
assert count == 26437, count
archive.unlink()
PY
RUN node /opt/velia-qualification/harness/lib/bin.js --version
COPY desktop/web/core.mjs /opt/velia-web/core.mjs
COPY desktop/web/tests /opt/velia-web/tests
RUN node --test /opt/velia-web/tests/*.test.mjs

FROM python:3.12-slim-bookworm AS checked
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app
COPY desktop/requirements-gateway.txt /app/desktop/requirements-gateway.txt
RUN pip install --no-cache-dir -r desktop/requirements-gateway.txt pytest==8.4.2
COPY velia_desktop_routes.py /app/velia_desktop_routes.py
COPY velia_request_understanding.py /app/velia_request_understanding.py
COPY desktop/gateway.py desktop/probe_gateway.py desktop/probe_request_intent.py desktop/answer_review.py desktop/web_routes.py desktop/feature_routes.py desktop/account_routes.py desktop/guest_routes.py desktop/guest_store.py desktop/web_search.py desktop/request_intent.py desktop/spelling_hints.py desktop/flash_worker_start.py /app/desktop/
COPY desktop/web /app/desktop/web
COPY tests/test_velia_desktop_gateway.py tests/test_velia_desktop_relay.py tests/test_velia_desktop_flash.py tests/test_velia_web_chat.py tests/test_velia_web_guest.py tests/test_velia_web_search.py tests/test_velia_request_understanding.py tests/test_velia_request_intent.py tests/test_velia_answer_review.py tests/test_velia_flash_worker_start.py tests/test_velia_desktop_flash_readiness.py /app/tests/
RUN python -m pytest -q -p no:cacheprovider tests/test_velia_desktop_gateway.py tests/test_velia_desktop_relay.py tests/test_velia_desktop_flash.py tests/test_velia_web_chat.py tests/test_velia_web_guest.py tests/test_velia_web_search.py tests/test_velia_request_understanding.py tests/test_velia_request_intent.py tests/test_velia_answer_review.py tests/test_velia_flash_worker_start.py tests/test_velia_desktop_flash_readiness.py

FROM python:3.12-slim-bookworm
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app
COPY desktop/requirements-gateway.txt /app/desktop/requirements-gateway.txt
RUN apt-get update && apt-get install -y --no-install-recommends libstdc++6 && rm -rf /var/lib/apt/lists/* && pip install --no-cache-dir -r desktop/requirements-gateway.txt && useradd --system --uid 10001 velia
COPY --from=qualification /usr/local/bin/node /usr/local/bin/node
COPY --from=qualification /opt/velia-qualification /opt/velia-qualification
COPY --from=checked /app/velia_desktop_routes.py /app/velia_desktop_routes.py
COPY --from=checked /app/velia_request_understanding.py /app/velia_request_understanding.py
COPY --from=checked /app/desktop /app/desktop
COPY desktop/scripts/probe-live-flash.mjs /app/desktop/scripts/probe-live-flash.mjs
COPY desktop/src/config.mjs desktop/src/proxy.mjs /app/desktop/src/
USER velia
RUN node /opt/velia-qualification/harness/lib/bin.js --version
EXPOSE 8080
CMD ["python", "-m", "desktop.gateway"]
