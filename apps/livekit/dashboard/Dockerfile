# LiveKit admin dashboard (thinhdanggroup/livekit-dashboard), built from one reviewed commit.
# Follows the project's own Dockerfile, with the base image pinned by digest.
FROM python:3.11-slim@sha256:e88e9763f943ec1834f992a4b51e0f24500486803e8bc534e5767af9ea65f6ce AS build

ARG REPOSITORY=https://github.com/thinhdanggroup/livekit-dashboard.git
ARG COMMIT=248b67c5afa9e1093eb00207bb63b0166923acb9

RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential git \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /src
RUN git init -q . \
 && git remote add origin "$REPOSITORY" \
 && git fetch -q --depth 1 origin "$COMMIT" \
 && git checkout -q FETCH_HEAD

WORKDIR /app
RUN cp /src/pyproject.toml /src/poetry.lock ./ \
 && pip install --no-cache-dir poetry \
 && poetry config virtualenvs.create false \
 && poetry install --without dev --no-root --no-interaction --no-ansi \
 && cp -r /src/app ./app

FROM python:3.11-slim@sha256:e88e9763f943ec1834f992a4b51e0f24500486803e8bc534e5767af9ea65f6ce AS runtime

RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY --from=build /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=build /usr/local/bin /usr/local/bin
COPY --from=build /app/app ./app
RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app
USER appuser
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
