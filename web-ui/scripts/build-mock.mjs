#!/usr/bin/env node
/** `npm run build:mock` — a static export with the fake gateway baked in (demo builds only). */
import { spawn } from 'node:child_process';

const child = spawn(
  process.platform === 'win32' ? 'npx.cmd' : 'npx',
  ['next', 'build'],
  {
    stdio: 'inherit',
    env: { ...process.env, NEXT_PUBLIC_MOCK: '1' },
    shell: process.platform === 'win32',
  },
);

child.on('exit', (code) => process.exit(code ?? 0));
