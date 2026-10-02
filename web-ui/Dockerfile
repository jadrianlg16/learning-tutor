# web-ui - Next.js 15 App Router, built as a **static export** and served by nginx.
#
# Why static and not `output: 'standalone'`: nothing in this UI needs a Node runtime. Every
# page is client-rendered and talks to the gateway over HTTP, so `next build` writes `out/`,
# which any file server can host - including the gateway itself from `LT_WEB_DIR`, which is
# the deployment CONTRACTS.md prefers. This image is the standalone-container option for the
# compose author; the `export` stage below is also the thing to copy out if you would rather
# hand `out/` to the gateway.
#
# Build args / env: see docs/modules/web-ui.md.

# --------------------------------------------------------------------- deps
FROM node:20-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm ci --omit=dev --ignore-scripts || npm install --omit=dev --ignore-scripts

# --------------------------------------------------------------------- build
FROM node:20-alpine AS build
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1

# Baked into the bundle at build time because the browser is what calls it. `/api` (same
# origin) is right for both deployments: nginx below proxies /api to the gateway, and the
# gateway serving out/ from LT_WEB_DIR already answers /api itself.
ARG NEXT_PUBLIC_GATEWAY_URL=/api
ENV NEXT_PUBLIC_GATEWAY_URL=$NEXT_PUBLIC_GATEWAY_URL

# Never 1 in an image you deploy - it swaps the real gateway for the in-browser fake.
ARG NEXT_PUBLIC_MOCK=0
ENV NEXT_PUBLIC_MOCK=$NEXT_PUBLIC_MOCK

COPY package.json package-lock.json* ./
RUN npm ci --ignore-scripts || npm install --ignore-scripts
COPY . .
RUN npm run build

# --------------------------------------------------------------------- export
# Nothing but the static site. Copy from this stage if the gateway is going to serve it:
#   docker build --target export -o type=local,dest=./web-out ./web-ui
FROM scratch AS export
COPY --from=build /app/out /

# --------------------------------------------------------------------- run
FROM nginx:alpine AS run

# Consumed by the official image's envsubst-on-templates entrypoint.
ENV WEB_PORT=8080
ENV GATEWAY_URL=http://gateway:5033

# DNS server for the lazy upstream lookup in the /api/ proxy. Left unset on purpose: the
# entrypoint snippet below defaults it to the resolver the container was actually given.
# ENV DNS_RESOLVER=

COPY docker-resolver.envsh /docker-entrypoint.d/18-lt-resolver.envsh
RUN chmod +x /docker-entrypoint.d/18-lt-resolver.envsh
COPY nginx.conf.template /etc/nginx/templates/default.conf.template
COPY --from=build /app/out /usr/share/nginx/html

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD wget -q -O /dev/null "http://127.0.0.1:${WEB_PORT}/" || exit 1
