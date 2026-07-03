# PlayNova Next.js App — Multi-stage Docker build for SPCS
# Build context: app/

# --- Stage 1: Builder ---
FROM node:20-slim AS builder
WORKDIR /app

COPY package.json package-lock.json* ./
RUN npm install --no-audit --no-fund

COPY . .

# Ensure tiles are in public for the build
# (tiles should be synced into app/public/tiles before docker build)
RUN npm run build

# --- Stage 2: Runtime ---
FROM node:20-slim AS runner
WORKDIR /app

ENV NODE_ENV=production
ENV PORT=8080
ENV HOSTNAME=0.0.0.0
ENV ORCHESTRATOR_URL=http://localhost:8080

# Copy standalone output
COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static
COPY --from=builder /app/public ./public

EXPOSE 8080

CMD ["node", "server.js"]
