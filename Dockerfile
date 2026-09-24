FROM python:3.12-slim
WORKDIR /app
COPY apk_netbridge.py /app/apk_netbridge.py
CMD ["python3", "-u", "/app/apk_netbridge.py"]
