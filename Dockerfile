FROM python:3.9
WORKDIR /app
# cache bust 3
COPY . .
RUN ls -la /app
RUN ls -la /app/db || echo "NO DB FOLDER"
RUN pip install -r requirements.txt
ENV PYTHONPATH=/app
CMD ["python", "app.py"]
