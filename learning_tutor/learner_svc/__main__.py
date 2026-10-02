"""``learner-svc`` console script: uvicorn over :func:`create_app`.

No machine-specific values. Host, port and data directory all come from the environment
with a default, and ``--host`` / ``--port`` override them for a one-off run.
"""

from __future__ import annotations

import argparse
import os

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 5034


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise SystemExit(f"{name} must be an integer, got {raw!r}") from exc


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="learner-svc", description="The learner model over HTTP (:5034 by default)."
    )
    parser.add_argument("--host", default=os.environ.get("LEARNER_HOST", DEFAULT_HOST))
    parser.add_argument("--port", type=int, default=_env_int("LEARNER_PORT", DEFAULT_PORT))
    parser.add_argument(
        "--data-dir", default=None, help="Override LT_DATA_DIR for this process."
    )
    parser.add_argument("--reload", action="store_true", help="Reload on source changes.")
    parser.add_argument(
        "--log-level", default=os.environ.get("LEARNER_LOG_LEVEL", "info")
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.data_dir:
        os.environ["LT_DATA_DIR"] = args.data_dir

    import uvicorn

    uvicorn.run(
        "learning_tutor.learner_svc.app:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level=args.log_level,
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
