"""`learner-svc`: the Stage 0 learner core behind HTTP (CONTRACTS.md, Stage 1)."""

from __future__ import annotations

from .app import create_app

__all__ = ["create_app"]
