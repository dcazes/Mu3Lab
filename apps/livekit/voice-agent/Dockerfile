
# Based on LiveKit's agent-starter-python template, changed to run fully on this server:
# no LiveKit Cloud models and no noise-cancellation cloud service.
# The uv base image is pinned by digest, matching the other Mu3Lab images.
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim@sha256:531f855bda2c73cd6ef67d56b733b357cea384185b3022bd09f05e002cd144ca AS build

ENV PYTHONUNBUFFERED=1
ENV UV_COMPILE_BYTECODE=1
# Models the agent downloads at build time live here, so the container needs no network later.
ENV HF_HOME=/app/.cache/huggingface

WORKDIR /app

# Dependencies first, so code-only changes reuse this layer.
# Only the dependencies: the agent runs as a script, so the project itself is never
# built (building it at start would need PyPI, which the agent's network can't reach).
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-install-project

# Downloads the local voice activity and turn-detection models the installed plugins need.
RUN .venv/bin/python -m livekit.agents download-files

COPY . .

FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim@sha256:531f855bda2c73cd6ef67d56b733b357cea384185b3022bd09f05e002cd144ca AS runtime

ENV PYTHONUNBUFFERED=1
ENV HF_HOME=/app/.cache/huggingface

ARG UID=10001
RUN adduser \
    --disabled-password \
    --gecos "" \
    --home "/app" \
    --shell "/sbin/nologin" \
    --uid "${UID}" \
    appuser

COPY --from=build --chown=appuser:appuser /app /app

WORKDIR /app
USER appuser

# "start" connects the worker to LiveKit and waits for rooms to dispatch to it.
# The venv's Python directly, not "uv run", which would try to sync (and reach PyPI) at start.
CMD ["/app/.venv/bin/python", "src/agent.py", "start"]
