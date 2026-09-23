FROM node:22.19.0-alpine3.22
RUN npm install --global --ignore-scripts mealie-mcp@0.2.105 supergateway@3.4.3
ENTRYPOINT ["supergateway"]
CMD ["--stdio", "mealie-mcp", "--outputTransport", "streamableHttp", "--port", "8000", "--healthEndpoint", "/healthz"]
