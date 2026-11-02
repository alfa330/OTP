# syntax=docker/dockerfile:1
# Фронт портала: сборка Vite, затем Caddy — статика, TLS от Let's Encrypt и прокси /api.
# Node 22 — как в CI (.github/workflows/deploy-pages.yml).
FROM node:22-bookworm-slim AS build

WORKDIR /src
# Куча V8 по умолчанию зависит от памяти контейнера; бандл App.jsx на 60 тыс. строк
# на тесной машине её исчерпывал.
ENV NODE_OPTIONS=--max-old-space-size=4096

COPY package.json package-lock.json ./
# ci, не install: ставит строго по локу и не правит package-lock.json (две машины).
RUN npm ci --no-audit --no-fund

COPY . .

# Адрес API и базовый путь зашиваются в бандл (src/App.jsx и ещё четыре входа читают
# import.meta.env.VITE_API_BASE_URL). Задаются из docker-compose.build.yml.
ARG VITE_API_BASE_URL
ARG VITE_BASE_PATH=/
ENV VITE_API_BASE_URL=${VITE_API_BASE_URL} \
    VITE_BASE_PATH=${VITE_BASE_PATH}
RUN npm run build


FROM caddy:2.10-alpine

COPY deploy/yandex/Caddyfile /etc/caddy/Caddyfile
COPY --from=build /src/dist /srv
