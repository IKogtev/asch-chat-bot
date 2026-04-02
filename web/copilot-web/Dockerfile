# syntax=docker/dockerfile:1.4
# BuildKit cache for npm speeds rebuilds; enable: DOCKER_BUILDKIT=1
FROM node:20-alpine

WORKDIR /app

# Verbose npm in Docker looks like a hang: after "http fetch" it extracts/links with no output.
ENV NPM_CONFIG_LOGLEVEL=warn \
    NODE_OPTIONS=--max-old-space-size=6144

COPY package.json package-lock.json .npmrc ./

# Use npm ci (deterministic) + cache mount so repeated builds do not re-download the tree.
RUN --mount=type=cache,target=/root/.npm \
    npm ci --no-audit --no-fund

COPY . .

ENV NEXT_TELEMETRY_DISABLED=1
RUN npm run build

EXPOSE 3000

ENV NODE_ENV=production
CMD ["npm", "run", "start"]
