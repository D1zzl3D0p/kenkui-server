FROM node:24.21.0-bookworm-slim
ENV NODE_ENV=production PORT=8080
WORKDIR /app
# Only restore-release.mjs output is accepted. Never npm install or rebuild source.
COPY --chown=node:node hosted-api/dist/standalone/ ./runtime/
COPY --chown=node:node hosted-api/dist/release-manifest.json ./release-manifest.json
COPY --chown=node:node deploy/neon/verify-standalone.mjs ./verify-standalone.mjs
RUN node verify-standalone.mjs /app/runtime /app/release-manifest.json
USER node
EXPOSE 8080
CMD ["node", "runtime/standalone.js", "--receipt", "/app/release-manifest.json"]
