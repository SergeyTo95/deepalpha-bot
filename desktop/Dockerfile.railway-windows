FROM electronuserland/builder@sha256:41ae540902461b6cbc988987db79547fcc10cda04d2a6c6367504f59d4b37c64 AS build

USER root
RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
       ca-certificates curl git unzip xz-utils musl-tools xvfb xauth python3 \
    && rm -rf /var/lib/apt/lists/*
RUN curl --fail --location --retry 3 https://nodejs.org/dist/v24.19.0/SHASUMS256.txt -o /tmp/node-shasums \
    && curl --fail --location --retry 3 https://nodejs.org/dist/v24.19.0/node-v24.19.0-linux-x64.tar.xz -o /tmp/node-v24.19.0-linux-x64.tar.xz \
    && cd /tmp \
    && awk '$2 == "node-v24.19.0-linux-x64.tar.xz" {print}' node-shasums > node-verified.sha256 \
    && test -s node-verified.sha256 \
    && sha256sum -c node-verified.sha256 \
    && tar -xJf node-v24.19.0-linux-x64.tar.xz -C /opt \
    && rm /tmp/node-v24.19.0-linux-x64.tar.xz

ENV PATH="/opt/node-v24.19.0-linux-x64/bin:${PATH}"
ENV WINEARCH=win64 WINEPREFIX=/opt/velia-wine WINEDEBUG=-all
ENV WINEDLLOVERRIDES="mscoree,mshtml=" CSC_IDENTITY_AUTO_DISCOVERY=false
WORKDIR /app/desktop
COPY desktop/package.json desktop/package-lock.json ./
RUN npm ci --ignore-scripts
COPY desktop/src/ ./src/
COPY desktop/scripts/ ./scripts/
COPY desktop/ui/ ./ui/
COPY desktop/tests/ ./tests/
RUN npm test \
    && python3 -m unittest discover -s tests -p 'test_artifact_server.py'
RUN npm run build:harness
ARG RAILWAY_GIT_COMMIT_SHA
ENV RAILWAY_GIT_COMMIT_SHA=${RAILWAY_GIT_COMMIT_SHA}
RUN xvfb-run -a sh -c 'wineboot --init && winecfg -v win10 && node scripts/package-windows-railway.mjs'

FROM python:3.12-slim-bookworm
WORKDIR /artifacts
COPY --from=build /app/desktop/dist/VELIA-Desktop-*.exe ./
COPY --from=build /app/desktop/dist/manifest.json ./
COPY desktop/scripts/serve-artifacts.py /usr/local/bin/serve-velia-artifacts.py
ENV PORT=8080 PYTHONUNBUFFERED=1
EXPOSE 8080
CMD ["python", "/usr/local/bin/serve-velia-artifacts.py"]
