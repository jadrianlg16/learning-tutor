#!/usr/bin/env node
/**
 * `npm run dev:mock` — Next dev server with the in-browser fake gateway switched on.
 *
 * Exists because `NEXT_PUBLIC_MOCK=1 next dev` is not portable: npm runs scripts through
 * cmd.exe on Windows, where the `VAR=value cmd` prefix is a syntax error. Setting the env
 * var in Node and spawning keeps the same command working on Windows and macOS.
 *
 * Port comes from PORT (default 3000) so nothing machine-specific is baked in.
 */
import { spawn } from 'node:child_process';

const port = process.env.PORT ?? '3000';
const args = ['dev', '--port', port];
if (process.env.HOSTNAME) args.push('--hostname', process.env.HOSTNAME);

const child = spawn(
  process.platform === 'win32' ? 'npx.cmd' : 'npx',
  ['next', ...args],
  {
    stdio: 'inherit',
    env: { ...process.env, NEXT_PUBLIC_MOCK: '1' },
    shell: process.platform === 'win32',
  },
);

child.on('exit', (code) => process.exit(code ?? 0));
