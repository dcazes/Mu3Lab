# Streaming speech recognition for the voice assistant: sherpa-onnx with streaming Zipformer models,
# on the processor. Adapted from the VoiceAgent project's stt-server (docs/voice-agent-improvements-plan.md, 2.3).
#
# The models are downloaded while this image is built on the server, from sherpa-onnx's own releases,
# each checked against its SHA-256; Mu3Lab does not redistribute them.
# - zipformer (2023-06-26, int8 files only): Apache-2.0. Writes capitals without punctuation.
# - kroko (2025-08-06): Kroko ASR by Banafo, CC BY-SA (https://huggingface.co/Banafo/Kroko-ASR).
#   Writes punctuation and capitals.
FROM python:3.13-slim-bookworm@sha256:f0fa218d5b06f9b9235024fa061cde2cb141f726de52134471053d2d9b5324a3 AS models
RUN apt-get update \
 && apt-get install -y --no-install-recommends bzip2 ca-certificates curl \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /models
ARG BASE=https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models
ARG KROKO=sherpa-onnx-streaming-zipformer-en-kroko-2025-08-06
ARG KROKO_SHA256=c8676e5ff9ac2a85296e53ee0fd4d5fb1db6770e7a7647166eeafe349ade6834
ARG ZIPFORMER=sherpa-onnx-streaming-zipformer-en-2023-06-26
ARG ZIPFORMER_SHA256=639e25b578e9e997131402199419c13a941f8e4e198e2da1ce57dbf5cf401282
RUN curl -fsSL -o kroko.tar.bz2 "$BASE/$KROKO.tar.bz2" \
 && echo "$KROKO_SHA256  kroko.tar.bz2" | sha256sum -c - \
 && tar xjf kroko.tar.bz2 && rm kroko.tar.bz2 \
 && mkdir kroko && mv "$KROKO"/*.onnx "$KROKO"/tokens.txt "$KROKO"/README.md kroko/ && rm -rf "$KROKO" \
 && curl -fsSL -o zipformer.tar.bz2 "$BASE/$ZIPFORMER.tar.bz2" \
 && echo "$ZIPFORMER_SHA256  zipformer.tar.bz2" | sha256sum -c - \
 && tar xjf zipformer.tar.bz2 && rm zipformer.tar.bz2 \
 && mkdir zipformer && mv "$ZIPFORMER"/*.int8.onnx "$ZIPFORMER"/tokens.txt zipformer/ \
 && mv "$ZIPFORMER"/test_wavs/0.wav /models/test.wav && rm -rf "$ZIPFORMER"

FROM python:3.13-slim-bookworm@sha256:f0fa218d5b06f9b9235024fa061cde2cb141f726de52134471053d2d9b5324a3
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
RUN pip install sherpa-onnx==2.0.1 websockets==17.4 numpy==2.5.4
RUN useradd --uid 10002 --no-create-home --shell /usr/sbin/nologin stt
COPY --from=models /models /models
COPY server.py /app/
WORKDIR /app
USER stt
ENV STT_MODELS=/models STT_HOST=0.0.0.0 STT_PORT=6006 STT_NUM_THREADS=1
HEALTHCHECK --interval=1m --timeout=5s --start-period=30s --start-interval=5s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:6006/health', timeout=3)"
CMD ["python", "server.py"]
