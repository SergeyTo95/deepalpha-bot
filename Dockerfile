# Isolated Desktop gateway preview; backend root Dockerfile stays in the feature PR.
FROM python:3.12-slim-bookworm AS checked
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app
COPY desktop/requirements-gateway.txt /app/desktop/requirements-gateway.txt
RUN pip install --no-cache-dir -r desktop/requirements-gateway.txt pytest==8.4.2
COPY velia_desktop_routes.py /app/velia_desktop_routes.py
COPY desktop/gateway.py desktop/probe_gateway.py /app/desktop/
COPY tests/test_velia_desktop_gateway.py tests/test_velia_desktop_relay.py /app/tests/
RUN python -m pytest -q -p no:cacheprovider tests/test_velia_desktop_gateway.py tests/test_velia_desktop_relay.py

FROM python:3.12-slim-bookworm
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app
COPY desktop/requirements-gateway.txt /app/desktop/requirements-gateway.txt
RUN pip install --no-cache-dir -r desktop/requirements-gateway.txt && useradd --system --uid 10001 velia
COPY --from=checked /app/velia_desktop_routes.py /app/velia_desktop_routes.py
COPY --from=checked /app/desktop /app/desktop
USER velia
EXPOSE 8080
CMD ["python", "-m", "desktop.gateway"]
