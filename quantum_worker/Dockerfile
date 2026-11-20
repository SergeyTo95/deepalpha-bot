FROM ubuntu:24.04

ARG LLAMA_COMMIT=abeada335e2e78bd3fe63febafab7e900ce75810

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates git cmake build-essential pkg-config libgomp1 python3 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/src
RUN git init llama.cpp \
    && cd llama.cpp \
    && git remote add origin https://github.com/ggml-org/llama.cpp.git \
    && git fetch --depth 1 origin "$LLAMA_COMMIT" \
    && git checkout --detach FETCH_HEAD \
    && test "$(git rev-parse HEAD)" = "$LLAMA_COMMIT" \
    && cmake -B build \
       -DCMAKE_BUILD_TYPE=Release \
       -DGGML_NATIVE=OFF \
       -DGGML_BACKEND_DL=ON \
       -DGGML_CPU_ALL_VARIANTS=ON \
    && cmake --build build -j"$(nproc)" --target llama-server

WORKDIR /opt/quantum
RUN cp -a /opt/src/llama.cpp/build/bin/. /opt/quantum/
COPY quantum_worker/start.py /opt/quantum/start.py

ENV PORT=8080 \
    PYTHONUNBUFFERED=1 \
    LD_LIBRARY_PATH=/opt/quantum \
    VELIA_QUANTUM_MODEL_PATH=/model/velia-quantum.gguf

VOLUME ["/model"]

CMD ["python3", "/opt/quantum/start.py"]
