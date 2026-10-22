# WhisperLiveKit, built from one reviewed release (v0.2.26) with its own locked dependencies.
# Follows upstream's Dockerfile.cpu, with every base image pinned by digest. CPU by default;
# docker-compose.nvidia.yml picks the cu129 extra. Upstream's GPU build starts from NVIDIA's CUDA
# images, but PyTorch's cu129 wheels already carry every CUDA library WhisperLiveKit uses, so the
# GPU build keeps the plain base and is about 4 GB smaller (and skips a 5 GB builder download).
ARG BASE=debian:bookworm-slim@sha256:7ff6ba31527b9e336162fa061746c366c4b2032f4d2cff2078bea43f78efe3f8

FROM ghcr.io/astral-sh/uv:0.12.10@sha256:2bb3ebca0a796a155094a27773d290c4b074572e6107f171d88d086682fd2500 AS uvbin

FROM ${BASE} AS build

ARG REPOSITORY=https://github.com/QuentinFuxa/WhisperLiveKit.git
# The v0.2.26 tag.
ARG COMMIT=aceed595a43e9eed5008c142d90a3f6e86747fbf
ARG EXTRAS=cpu

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1
RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential python3-dev git ca-certificates \
 && rm -rf /var/lib/apt/lists/*

COPY --from=uvbin /uv /uvx /bin/
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_NO_DEV=1
ENV UV_PYTHON_PREFERENCE=only-managed
ENV UV_PYTHON_INSTALL_DIR=/python
RUN uv python install 3.12

WORKDIR /app
# The lock file resolves an optional extra from the third_party submodule, so fetch it too.
RUN git init -q . \
 && git remote add origin "$REPOSITORY" \
 && git fetch -q --depth 1 origin "$COMMIT" \
 && git checkout -q FETCH_HEAD \
 && git submodule update -q --init --depth 1 \
 && rm -rf .git

RUN set -eux; \
  set --; \
  for extra in $(echo "$EXTRAS" | tr ',' ' '); do set -- "$@" --extra "$extra"; done; \
  uv sync --frozen --no-editable --no-cache "$@"

FROM ${BASE} AS runtime

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
 && rm -rf /var/lib/apt/lists/*

COPY --from=build /python /python
COPY --from=build /app/.venv /app/.venv

# Runs as its own user; compose's models-init gives it the models folder.
RUN useradd --system --uid 10001 --home-dir /app --shell /usr/sbin/nologin wlk
USER wlk
WORKDIR /app
ENV PATH="/app/.venv/bin:$PATH"
# CTranslate2 (the fast Whisper encoder) finds cuBLAS and cuDNN in the wheels PyTorch installed.
# The NVIDIA runtime adds the driver for containers given a GPU; on the CPU build these paths are empty.
ENV LD_LIBRARY_PATH=/app/.venv/lib/python3.12/site-packages/nvidia/cublas/lib:/app/.venv/lib/python3.12/site-packages/nvidia/cudnn/lib:/app/.venv/lib/python3.12/site-packages/nvidia/cuda_runtime/lib:/app/.venv/lib/python3.12/site-packages/nvidia/cuda_nvrtc/lib
ENV NVIDIA_VISIBLE_DEVICES=all
ENV NVIDIA_DRIVER_CAPABILITIES=compute,utility
ENV PYTHONUNBUFFERED=1
ENV UV_PYTHON_DOWNLOADS=0
EXPOSE 8000

ENTRYPOINT ["wlk", "--host", "0.0.0.0"]
CMD ["--model", "small"]
