FROM node:22.19.0-alpine3.22
RUN apk add --no-cache python3 py3-pip \
    && pip3 install --break-system-packages --no-cache-dir mcpo==0.0.18
ENTRYPOINT ["mcpo", "--host", "0.0.0.0", "--port", "8000", "--"]
CMD ["npx", "-y", "mealie-mcp@0.2.105"]
