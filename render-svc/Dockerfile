# render-svc: Mermaid -> SVG, Mermaid syntax check, LaTeX (KaTeX) validation.
#
# No headless browser here (see docs/modules/render-svc.md for the library
# investigation): rendering uses `beautiful-mermaid` (pure JS + elkjs layout),
# and syntax checking uses the real `mermaid` parser with a `jsdom` shim
# (a DOM *implementation*, not a browser -- no Chromium, no Playwright).
# That keeps this image a plain node:20-alpine, not the ~1GB+ mermaid-cli /
# Puppeteer base image.
FROM node:20-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --omit=dev --no-audit --no-fund

FROM node:20-alpine AS runtime
WORKDIR /app
ENV NODE_ENV=production
RUN addgroup -S render && adduser -S render -G render
COPY --from=deps /app/node_modules ./node_modules
COPY package.json ./
COPY src ./src

USER render

ARG RENDER_PORT=5035
ENV RENDER_PORT=${RENDER_PORT}
EXPOSE 5035

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD node -e "fetch('http://127.0.0.1:'+(process.env.RENDER_PORT||5035)+'/healthz').then(r=>{if(!r.ok)throw new Error(r.status)}).catch(()=>process.exit(1))"

CMD ["node", "src/server.js"]
