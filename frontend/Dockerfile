# syntax=docker/dockerfile:1

FROM node:22-alpine AS base
WORKDIR /app

# ---- deps: 의존성 설치는 한 번만 ----
FROM base AS deps
COPY package.json package-lock.json ./
RUN npm ci

# ---- dev: 소스는 compose 가 마운트한다. /app/node_modules 는 익명 볼륨으로 보호 ----
FROM base AS dev
COPY --from=deps /app/node_modules ./node_modules
COPY . .
ENV NEXT_TELEMETRY_DISABLED=1
EXPOSE 3000
CMD ["npm", "run", "dev"]

# ---- builder ----
FROM base AS builder
COPY --from=deps /app/node_modules ./node_modules
COPY . .
ENV NEXT_TELEMETRY_DISABLED=1
RUN npm run build

# ---- prod: standalone 출력만 실어 나른다 ----
FROM base AS prod
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    HOSTNAME=0.0.0.0 \
    PORT=3000
COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static
COPY --from=builder /app/public ./public
USER node
EXPOSE 3000
CMD ["node", "server.js"]
