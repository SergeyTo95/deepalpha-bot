FROM pytorch/pytorch:2.1.2-cuda12.1-cudnn8-runtime

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    RADAR_UPSTREAM_ROOT=/opt/damo-radar \
    RADAR_MODEL_ROOT=/models/radar \
    VELIA_MEDICAL_WORK_DIR=/var/lib/velia-medical

RUN apt-get update && apt-get install -y --no-install-recommends \
      git ca-certificates libmagic1 libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

COPY medical_worker/upstream_source.py /opt/velia/fetch_radar_source.py
RUN python /opt/velia/fetch_radar_source.py /opt/damo-radar

WORKDIR /app
COPY medical_worker /app/medical_worker

RUN pip install --no-cache-dir -c /app/medical_worker/constraints.txt -r /opt/damo-radar/requirements.txt \
    && pip install --no-cache-dir -c /app/medical_worker/constraints.txt -r /app/medical_worker/requirements.txt \
    && pip check

RUN mkdir -p /models/radar /var/lib/velia-medical
WORKDIR /app/medical_worker
EXPOSE 8088
CMD ["python", "app.py"]
