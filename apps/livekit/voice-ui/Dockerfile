# LiveKit's agent-starter-react, the page people talk to the assistant on.
# Built from one reviewed commit; the base image is pinned by digest.
FROM node:24-alpine@sha256:44fce696f38ba8c987c4db89954051000bbd05ffd8d8659ba15ed4a8602e937f AS build

ARG REPOSITORY=https://github.com/livekit-examples/agent-starter-react.git
ARG COMMIT=9e1fc2f40fd7ceaccbe4a872b21765b49eefea80

RUN apk add --no-cache git
WORKDIR /app
RUN git init -q . \
 && git remote add origin "$REPOSITORY" \
 && git fetch -q --depth 1 origin "$COMMIT" \
 && git checkout -q FETCH_HEAD \
 && rm -rf .git

# The page reads LIVEKIT_URL to choose between this server and LiveKit's hosted demo agent.
# Read it on every request, never once at build time when it is not set yet.
RUN printf "\nexport const dynamic = 'force-dynamic';\n" >> app/page.tsx

RUN corepack enable && pnpm install --frozen-lockfile && pnpm build

FROM node:24-alpine@sha256:44fce696f38ba8c987c4db89954051000bbd05ffd8d8659ba15ed4a8602e937f AS runtime

ENV NODE_ENV=production
ENV NEXT_TELEMETRY_DISABLED=1
WORKDIR /app
COPY --from=build --chown=node:node /app /app
USER node
EXPOSE 3000
CMD ["node_modules/.bin/next", "start", "-H", "0.0.0.0", "-p", "3000"]
