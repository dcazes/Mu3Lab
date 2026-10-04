FROM node:22.19.0-alpine3.22@sha256:d2166de198f26e17e5a442f537754dd616ab069c47cc57b889310a717e0abbf9
WORKDIR /opt/connector
COPY package.json package-lock.json ./
RUN npm ci --omit=dev --ignore-scripts
ENV PATH="/opt/connector/node_modules/.bin:$PATH"
USER node
ENTRYPOINT ["supergateway"]
# immich-mcp 0.5.2's `immich-mcp` launcher starts its server twice on one stdio,
# so every tool call would run twice. dist/index.js starts it once.
CMD ["--stdio", "node /opt/connector/node_modules/immich-mcp/dist/index.js", "--outputTransport", "streamableHttp", "--stateful", "--sessionTimeout", "900000", "--port", "8000", "--healthEndpoint", "/healthz"]
