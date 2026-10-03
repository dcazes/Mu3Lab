# syntax=docker.io/docker/dockerfile:1
FROM python:3.12.11-slim@sha256:47ae396f09c1303b8653019811a8498470603d7ffefc29cb07c88f1f8cb3d19f

ARG SURFSENSE_REV=c139e191a831bab76b9afc4f9b2063f2175be109
ARG SURFSENSE_ARCHIVE_SHA256=6d03f0ea826ad27197c350c5862b6cfe96e8106eae3218ca46da40a683216863

RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl \
    && curl -fsSL "https://github.com/MODSetter/SurfSense/archive/${SURFSENSE_REV}.tar.gz" -o /tmp/surfsense.tar.gz \
    && echo "${SURFSENSE_ARCHIVE_SHA256}  /tmp/surfsense.tar.gz" | sha256sum -c - \
    && mkdir -p /opt/surfsense \
    && tar -xzf /tmp/surfsense.tar.gz --strip-components=2 -C /opt/surfsense "SurfSense-${SURFSENSE_REV}/surfsense_mcp" \
    && pip install --no-cache-dir uv==0.8.22 \
    && cd /opt/surfsense && uv export --frozen --no-dev --no-emit-project --no-hashes --format requirements-txt -o /tmp/requirements.txt \
    && pip install --no-cache-dir -r /tmp/requirements.txt \
    && pip install --no-cache-dir --no-deps /opt/surfsense \
    && rm -f /tmp/surfsense.tar.gz /tmp/requirements.txt \
    && apt-get purge -y curl && apt-get autoremove -y && rm -rf /var/lib/apt/lists/* \
    && useradd --system --uid 10001 --home-dir /nonexistent --shell /usr/sbin/nologin surfsense

USER 10001:10001
ENV PYTHONUNBUFFERED=1 \
    SURFSENSE_MCP_TRANSPORT=streamable-http \
    SURFSENSE_MCP_HOST=0.0.0.0 \
    SURFSENSE_MCP_PORT=8080
EXPOSE 8080
CMD ["python", "-m", "mcp_server"]
