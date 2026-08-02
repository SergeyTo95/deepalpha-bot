ARG VELYON_MEMORY_BASE_IMAGE=agentmemory/memory-core:latest
FROM ${VELYON_MEMORY_BASE_IMAGE}

USER root

RUN mkdir -p /data/config /data/tdai-memory \
    && chown -R 10001:10001 /data

COPY --chown=10001:10001 tdai-gateway.yaml /data/config/tdai-gateway.yaml

ENV TDAI_GATEWAY_CONFIG=/data/config/tdai-gateway.yaml \
    TDAI_GATEWAY_HOST=0.0.0.0 \
    TDAI_GATEWAY_PORT=8420 \
    TDAI_DATA_DIR=/data/tdai-memory \
    TDAI_DEPLOY_MODE=standalone \
    STATE_BACKEND=local

USER 10001

EXPOSE 8420

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=5 \
    CMD node -e "fetch('http://127.0.0.1:8420/health').then(r=>{if(!r.ok)process.exit(1)}).catch(()=>process.exit(1))"

# The upstream image entrypoint and command are deliberately inherited.
# Secrets are supplied only through Railway service variables at runtime.
