import path from 'node:path';
import type { NextConfig } from 'next';

/**
 * Static export. The whole UI is client-rendered and talks to the gateway over HTTP,
 * so there is nothing that needs a Node server at runtime: `next build` writes `out/`
 * and the gateway (or the nginx image in ./Dockerfile) can serve it as plain files
 * from `LT_WEB_DIR`.
 *
 * `trailingSlash: true` makes every route a directory with an `index.html`, which is
 * what a dumb static file server needs to resolve `/goal/map` without rewrite rules.
 */
const nextConfig: NextConfig = {
  output: 'export',
  trailingSlash: true,
  // This app is its own npm workspace inside a Python repo. Pin the tracing root to this
  // directory so Next does not walk up and pick a lockfile from somewhere else on the host.
  outputFileTracingRoot: path.resolve(process.cwd()),
  images: { unoptimized: true },
  env: {
    // Surfaced so the client bundle can branch without reading process.env at runtime.
    NEXT_PUBLIC_MOCK: process.env.NEXT_PUBLIC_MOCK ?? '0',
  },
};

export default nextConfig;
