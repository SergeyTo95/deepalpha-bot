FROM ubuntu:24.04
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl python3 libgomp1 \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /opt/bonsai
# Official demo's tested runtime. Stock llama.cpp cannot load Bonsai 2.
RUN curl -fL --retry 3 --max-time 300 \
    https://github.com/PrismML-Eng/llama.cpp/releases/download/prism-b10709-9a9394a/llama-prism-b10709-9a9394a-bin-ubuntu-x64.tar.gz -o runtime.tar.gz \
    && echo '48b487f00fd2b27bc3ef77c701b43c1c23a4af484d2a203ae87d0efc41506728  runtime.tar.gz' | sha256sum -c - \
    && tar --no-same-owner -xzf runtime.tar.gz --strip-components=1 \
    && rm runtime.tar.gz
RUN curl -fL --retry 3 --max-time 1800 \
    https://huggingface.co/prism-ml/Ternary-Bonsai-2-27B-gguf/resolve/6ed5e12bf84b7a63069882c91dd9e9218647d17b/Ternary-Bonsai-2-27B-PTQ1_0.gguf \
    -o model.gguf \
    && echo '53107f530aa52eb00912263ab1ee29bd199261c87cd7b4ad4ca1318c1fe33ee3  model.gguf' | sha256sum -c -
COPY start.py /opt/bonsai/start.py
COPY probe.py /opt/bonsai/probe.py
ENV PORT=8080 PYTHONUNBUFFERED=1
CMD ["python3", "/opt/bonsai/start.py"]
