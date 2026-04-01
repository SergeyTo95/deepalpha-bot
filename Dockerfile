FROM python:3.9
WORKDIR /app
COPY . .
RUN pip install -r requirements.txt
RUN find /app -name "*.py" | head -30
ENV PYTHONPATH=/app
CMD ["python", "app.py"]
