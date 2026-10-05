FROM node:22.19.0-alpine3.22@sha256:d2166de198f26e17e5a442f537754dd616ab069c47cc57b889310a717e0abbf9
WORKDIR /opt/connector
COPY package.json package-lock.json ./
RUN npm ci --omit=dev --ignore-scripts
# mcp-grocy reads its tool switches from the working directory.
COPY mcp-grocy.yaml /home/node/mcp-grocy.yaml
WORKDIR /home/node
USER node
ENTRYPOINT ["/opt/connector/node_modules/.bin/mcp-grocy"]
