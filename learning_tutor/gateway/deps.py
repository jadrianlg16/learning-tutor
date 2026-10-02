"""What every route needs, built once at startup and hung off ``app.state``."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from fastapi import Depends, Header, Request

from ..config import Settings
from ..tutor.prompts import PromptPack, load_pack
from .clients import LearnerClient, RenderClient
from .state import GatewayState


@dataclass
class Services:
    settings: Settings
    state: GatewayState
    learner: LearnerClient
    render: RenderClient
    pack: PromptPack

    @property
    def prompt_version(self) -> str:
        return self.pack.prompt_version

    @property
    def rubric_version(self) -> str:
        return self.pack.rubric_version

    def close(self) -> None:
        self.learner.close()
        self.render.close()


def build_services(
    settings: Settings,
    *,
    learner_transport: httpx.BaseTransport | None = None,
    render_transport: httpx.BaseTransport | None = None,
    learner_url: str | None = None,
    render_url: str | None = None,
) -> Services:
    settings.ensure_dirs()
    state_path = Path(settings.data_dir) / "gateway" / "state.json"
    return Services(
        settings=settings,
        state=GatewayState(state_path),
        learner=LearnerClient(learner_url, transport=learner_transport),
        render=RenderClient(render_url, transport=render_transport),
        pack=load_pack(),
    )


def get_services(request: Request) -> Services:
    return request.app.state.services


#: CONTRACTS.md: "Every mutating route accepts an ``Idempotency-Key`` header" — and passes
#: it through to learner-svc, which is where the replay table lives.
IdempotencyHeader = Header(default=None, alias="Idempotency-Key")


def scoped_key(key: str | None, suffix: str) -> str | None:
    """One client key fans out to several learner-svc writes; each needs its own key."""

    return f"{key}:{suffix}" if key else None


ServicesDep = Depends(get_services)


def as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}
