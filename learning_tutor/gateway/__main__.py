"""The `learn-gateway` console script.

Port and host come from the environment with a default, per CONTRACTS.md's "no
machine-specific values" rule; the flags override the environment for a one-off run.
"""

from __future__ import annotations

import argparse
import os

import uvicorn

from .app import DEFAULT_PORT


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise SystemExit(f"{name} must be an integer, got {raw!r}") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="learn-gateway", description="Learning Tutor gateway")
    parser.add_argument("--host", default=os.environ.get("GATEWAY_HOST") or "127.0.0.1")
    parser.add_argument("--port", type=int, default=_env_int("GATEWAY_PORT", DEFAULT_PORT))
    parser.add_argument("--reload", action="store_true")
    parser.add_argument("--log-level", default=os.environ.get("GATEWAY_LOG_LEVEL") or "info")
    args = parser.parse_args(argv)

    uvicorn.run(
        "learning_tutor.gateway.app:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level=args.log_level,
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
