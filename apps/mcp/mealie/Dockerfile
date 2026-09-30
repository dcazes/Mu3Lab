FROM node:22.19.0-alpine3.22@sha256:d2166de198f26e17e5a442f537754dd616ab069c47cc57b889310a717e0abbf9
RUN npm install --global --ignore-scripts mealie-mcp@0.2.105 supergateway@3.4.3
ENTRYPOINT ["supergateway"]
CMD ["--stdio", "mealie-mcp", "--outputTransport", "streamableHttp", "--port", "8000", "--healthEndpoint", "/healthz"]
