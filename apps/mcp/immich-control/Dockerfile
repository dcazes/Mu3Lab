FROM node:22.19.0-alpine3.22
RUN npm install --global --ignore-scripts immich-mcp@0.5.2 supergateway@3.4.3
USER node
ENTRYPOINT ["supergateway"]
# immich-mcp 0.5.2's `immich-mcp` launcher starts its server twice on one stdio,
# so every tool call would run twice. dist/index.js starts it once.
CMD ["--stdio", "node /usr/local/lib/node_modules/immich-mcp/dist/index.js", "--outputTransport", "streamableHttp", "--stateful", "--sessionTimeout", "900000", "--port", "8000", "--healthEndpoint", "/healthz"]
