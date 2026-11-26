FROM python:3.11
WORKDIR /app
COPY requirements.txt .
COPY vendor/aiogram_compat ./vendor/aiogram_compat
RUN python vendor/aiogram_compat/verify_upstream.py
RUN pip install --upgrade pip setuptools && pip install -r requirements.txt && pip check
ENV LITEPARSE_MODEL_DIR=/opt/velia/liteparse/models
ARG VELIA_ATTACHMENTS_OCRIA_ENABLED=0
RUN if [ "$VELIA_ATTACHMENTS_OCRIA_ENABLED" = 1 ]; then \
      pip install --no-cache-dir liteparse==2.21.2 && \
      lit models pull ocria-nano@1.0.0 && \
      pip check; \
    fi
COPY . .
RUN sh ci/install-velia-pr-review.sh /usr/local/bin/velia-open-code-review
RUN ls -la /app
RUN ls -la /app/db || echo "NO DB FOLDER"
RUN ls -la /app/bot/
ENV PYTHONPATH=/app
RUN if [ "$VELIA_ATTACHMENTS_OCRIA_ENABLED" = 1 ]; then \
      python ci/probe-velia-document-recognition.py; \
    fi
RUN pip install supervisor --break-system-packages
RUN mkdir -p /etc/supervisor/conf.d
COPY supervisord.conf /etc/supervisor/conf.d/supervisord.conf
CMD ["supervisord", "-c", "/etc/supervisor/conf.d/supervisord.conf"]
