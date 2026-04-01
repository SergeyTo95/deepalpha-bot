FROM python:3.9
WORKDIR /app
# cache bust 2
COPY . .
RUN pip install -r requirements.txt
ENV PYTHONPATH=/app
CMD ["python", "app.py"]
