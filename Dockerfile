FROM python:3.9
WORKDIR /app
# bust cache 99
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
RUN ls -la /app
RUN ls -la /app/db || echo "NO DB FOLDER"
RUN ls -la /app/bot/
ENV PYTHONPATH=/app
CMD ["python", "app.py"]
