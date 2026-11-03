FROM node:24.21.0-bookworm-slim AS build
WORKDIR /build
COPY hosted-api/package.json hosted-api/package-lock.json ./
RUN npm ci --ignore-scripts
COPY hosted-api/build.mjs ./
COPY hosted-api/src ./src
RUN npm run build

FROM node:24.21.0-bookworm-slim
ENV NODE_ENV=production PORT=8080
WORKDIR /app
COPY --from=build --chown=node:node /build/dist/standalone/ ./
USER node
EXPOSE 8080
CMD ["node", "standalone.js"]
