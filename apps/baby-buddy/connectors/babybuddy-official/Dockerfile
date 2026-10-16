# syntax=docker/dockerfile:1.7
# Baby Buddy's official MCP server at a pinned commit, built with its own lock file.
FROM python:3.13-slim@sha256:70729b46c69b4f1e97c4822c1af3df53a1476cf5ddc6c087c0c10bc3a5678c2f
WORKDIR /app
ADD --keep-git-dir=false https://github.com/babybuddy/babybuddy-mcp.git#9f32d446241c323602b23160d05e926161184d9f /app
RUN pip install --no-cache-dir uv==0.8.22 \
    && uv sync --frozen --no-dev --no-install-project \
    && pip uninstall -y uv
ENV PATH="/app/.venv/bin:$PATH" PYTHONPATH="/app/src" MCP_TRANSPORT=http MCP_HOST=0.0.0.0 MCP_PORT=8080
USER 65534:65534
EXPOSE 8080
CMD ["python", "-m", "babybuddy_mcp"]
