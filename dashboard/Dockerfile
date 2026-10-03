# syntax=docker/dockerfile:1
# Scaffold dashboard image — Vite production build served by non-root nginx.
# VITE_* values are BAKED IN at build time (Vite inlines them), so pass the
# browser-visible engine URL as a build arg (see repo-root docker-compose.yml).
# Build:  docker build -t scaffold-dashboard --build-arg VITE_ENGINE_URL=https://api.example.com ./dashboard

# ---------------------------------------------------------------------------
# Stage 1: production build
# ---------------------------------------------------------------------------
FROM node:22-alpine AS build

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY . .

ARG VITE_ENGINE_URL=http://localhost:8000
ARG VITE_SUPABASE_URL=
ARG VITE_SUPABASE_ANON_KEY=
ENV VITE_ENGINE_URL=$VITE_ENGINE_URL \
    VITE_SUPABASE_URL=$VITE_SUPABASE_URL \
    VITE_SUPABASE_ANON_KEY=$VITE_SUPABASE_ANON_KEY

RUN npm run build

# ---------------------------------------------------------------------------
# Stage 2: static server (unprivileged nginx listens on 8080 as non-root)
# ---------------------------------------------------------------------------
FROM nginxinc/nginx-unprivileged:1.27-alpine AS runtime

LABEL org.opencontainers.image.title="scaffold-dashboard" \
      org.opencontainers.image.description="Scaffold dashboard — project awareness UI" \
      org.opencontainers.image.source="https://github.com/themdmohsin/Scaffold"

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD wget -q -O /dev/null http://127.0.0.1:8080/ || exit 1
