FROM pytorch/pytorch:2.1.2-cuda12.1-cudnn8-runtime

ARG RADAR_COMMIT=9319f36642b6f3f4708c8e5c8844ab114d6e7b24
ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    RADAR_UPSTREAM_ROOT=/opt/damo-radar \
    RADAR_MODEL_ROOT=/models/radar \
    VELIA_MEDICAL_WORK_DIR=/var/lib/velia-medical

RUN apt-get update && apt-get install -y --no-install-recommends \
      git ca-certificates libmagic1 libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

RUN git clone https://github.com/alibaba-damo-academy/damo-radar.git /opt/damo-radar \
    && cd /opt/damo-radar \
    && git checkout "$RADAR_COMMIT" \
    && test "$(git rev-parse HEAD)" = "$RADAR_COMMIT"

RUN pip install --no-cache-dir -r /opt/damo-radar/requirements.txt \
    && pip install --no-cache-dir -r /app/medical_worker/requirements.txt

WORKDIR /app
COPY medical_worker /app/medical_worker

RUN mkdir -p /models/radar /var/lib/velia-medical
WORKDIR /app/medical_worker
EXPOSE 8088
CMD ["python", "app.py"]
