FROM python:3.11
WORKDIR /app
COPY requirements.txt .
COPY vendor/aiogram_compat ./vendor/aiogram_compat
RUN python vendor/aiogram_compat/verify_upstream.py
RUN pip install --upgrade pip setuptools && pip install -r requirements.txt && pip check
COPY . .
RUN ls -la /app
RUN ls -la /app/db || echo "NO DB FOLDER"
RUN ls -la /app/bot/
ENV PYTHONPATH=/app
RUN pip install supervisor --break-system-packages
RUN mkdir -p /etc/supervisor/conf.d
COPY supervisord.conf /etc/supervisor/conf.d/supervisord.conf
CMD ["supervisord", "-c", "/etc/supervisor/conf.d/supervisord.conf"]
