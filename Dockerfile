FROM ubuntu:24.04
# AVX2 canary: pinned post-PR206 runtime

ARG LLAMA_COMMIT=01ae597e3f7d4742909e1e831abb12fe3d24b2cf

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl python3 git cmake build-essential pkg-config libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/src
RUN git clone https://github.com/PrismML-Eng/llama.cpp.git \
    && cd llama.cpp \
    && git checkout "$LLAMA_COMMIT" \
    && test "$(git rev-parse HEAD)" = "$LLAMA_COMMIT" \
    && cmake -B build \
       -DCMAKE_BUILD_TYPE=Release \
       -DGGML_NATIVE=OFF \
       -DGGML_CPU_ALL_VARIANTS=ON \
    && cmake --build build -j"$(nproc)" --target llama-server

WORKDIR /opt/bonsai
RUN cp -a /opt/src/llama.cpp/build/bin/. /opt/bonsai/

RUN curl -fL --retry 3 --max-time 1800 \
    https://huggingface.co/prism-ml/Ternary-Bonsai-2-27B-gguf/resolve/6ed5e12bf84b7a63069882c91dd9e9218647d17b/Ternary-Bonsai-2-27B-PQ2_0.gguf \
    -o model.gguf \
    && echo '3907dc1658db1f78a9826bf8d5bcb8dc65db0d466388937af57f2294fae62ec1  model.gguf' | sha256sum -c -

COPY start.py /opt/bonsai/start.py
COPY probe.py /opt/bonsai/probe.py

ENV PORT=8080 \
    PYTHONUNBUFFERED=1 \
    LD_LIBRARY_PATH=/opt/bonsai

CMD ["python3", "/opt/bonsai/start.py"]
