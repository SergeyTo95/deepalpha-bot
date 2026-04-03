FROM python:3.9
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
RUN ls -la /app
RUN ls -la /app/db || echo "NO DB FOLDER"
RUN ls -la /app/bot/
ENV PYTHONPATH=/app
RUN pip install supervisor --break-system-packages
RUN mkdir -p /etc/supervisor/conf.d
COPY supervisord.conf /etc/supervisor/conf.d/supervisord.conf
CMD ["supervisord", "-c", "/etc/supervisor/conf.d/supervisord.conf"]
