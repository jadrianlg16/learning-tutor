"""``telegram/state.json`` — the small operational log the policy reads.

This is *not* the learner event log (that stays in ``learner/events.db``, append-only,
written only through the ``learner`` CLI). This file tracks what the policy itself needs
that the event log doesn't carry: which push is still waiting for a tap, the Telegram
``message_id`` to edit once it resolves, the unanswered streak, the paused flag, and the
``getUpdates`` offset. It is safe to delete — the worst case is one re-asked question and a
reset streak, never lost learner evidence.

Statuses on a :class:`PushRecord`: ``pending`` (sent, no tap yet), ``answered`` (an option or
IDK was tapped and recorded), ``not_now`` (dismissed — data, not a wrong answer), ``expired``
(nobody tapped it before the next opportunity).
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

PENDING = "pending"
ANSWERED = "answered"
NOT_NOW = "not_now"
EXPIRED = "expired"

RESOLVED_STATUSES = {ANSWERED, NOT_NOW, EXPIRED}


@dataclass
class PushRecord:
    """One Telegram push and what became of it."""

    token: str
    item_id: str
    item_version_id: str | None
    node_id: str | None
    node_title: str | None
    session_id: str | None
    sent_at: str  # ISO 8601
    stem: str | None = None
    answer_key: str | None = None
    options: list[str] = field(default_factory=list)
    context: str = "delayed"
    status: str = PENDING
    message_id: int | None = None
    resolved_at: str | None = None
    correct: bool | None = None
    idk: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "token": self.token,
            "item_id": self.item_id,
            "item_version_id": self.item_version_id,
            "node_id": self.node_id,
            "node_title": self.node_title,
            "session_id": self.session_id,
            "sent_at": self.sent_at,
            "stem": self.stem,
            "answer_key": self.answer_key,
            "options": self.options,
            "context": self.context,
            "status": self.status,
            "message_id": self.message_id,
            "resolved_at": self.resolved_at,
            "correct": self.correct,
            "idk": self.idk,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PushRecord:
        return cls(
            token=data["token"],
            item_id=data["item_id"],
            item_version_id=data.get("item_version_id"),
            node_id=data.get("node_id"),
            node_title=data.get("node_title"),
            session_id=data.get("session_id"),
            sent_at=data["sent_at"],
            stem=data.get("stem"),
            answer_key=data.get("answer_key"),
            options=list(data.get("options") or []),
            context=data.get("context", "delayed"),
            status=data.get("status", PENDING),
            message_id=data.get("message_id"),
            resolved_at=data.get("resolved_at"),
            correct=data.get("correct"),
            idk=bool(data.get("idk", False)),
        )


# Cap on how many pushes state.json keeps. Policy only ever looks back
# ``kill_window_days`` (14 by default); this is a generous multiple of a 3/day budget
# over that window so the file never grows unbounded, without needing a second store.
MAX_PUSHES_KEPT = 200


@dataclass
class PushHistory:
    """Everything the policy functions need, read from ``telegram/state.json``."""

    pushes: list[PushRecord] = field(default_factory=list)
    paused: bool = False
    paused_reason: str | None = None
    unanswered_streak: int = 0
    live_session_dates: list[str] = field(default_factory=list)
    update_offset: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "pushes": [p.to_dict() for p in self.pushes[-MAX_PUSHES_KEPT:]],
            "paused": self.paused,
            "paused_reason": self.paused_reason,
            "unanswered_streak": self.unanswered_streak,
            "live_session_dates": self.live_session_dates,
            "update_offset": self.update_offset,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PushHistory:
        return cls(
            pushes=[PushRecord.from_dict(p) for p in data.get("pushes", [])],
            paused=bool(data.get("paused", False)),
            paused_reason=data.get("paused_reason"),
            unanswered_streak=int(data.get("unanswered_streak", 0)),
            live_session_dates=list(data.get("live_session_dates") or []),
            update_offset=data.get("update_offset"),
        )

    def pending(self) -> PushRecord | None:
        for push in reversed(self.pushes):
            if push.status == PENDING:
                return push
        return None

    def find(self, token: str) -> PushRecord | None:
        for push in self.pushes:
            if push.token == token:
                return push
        return None

    def sent_on(self, day: str) -> list[PushRecord]:
        return [p for p in self.pushes if p.sent_at[:10] == day]

    def last_sent_at(self) -> datetime | None:
        if not self.pushes:
            return None
        return max(_parse(p.sent_at) for p in self.pushes)

    def last_response_at(self) -> datetime | None:
        """Timestamp of the most recent tap of any kind (answered, idk or not_now)."""

        responses = [
            _parse(p.resolved_at)
            for p in self.pushes
            if p.status in (ANSWERED, NOT_NOW) and p.resolved_at
        ]
        return max(responses) if responses else None


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def state_path(data_dir: str | os.PathLike[str]) -> Path:
    return Path(data_dir) / "telegram" / "state.json"


def load(data_dir: str | os.PathLike[str]) -> PushHistory:
    path = state_path(data_dir)
    if not path.exists():
        return PushHistory()
    try:
        return PushHistory.from_dict(json.loads(path.read_text(encoding="utf-8")))
    except (json.JSONDecodeError, KeyError, TypeError):
        # A corrupted operational file must never take down the tick; start clean.
        return PushHistory()


def save(data_dir: str | os.PathLike[str], history: PushHistory) -> Path:
    path = state_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp", prefix=".state_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(history.to_dict(), fh, indent=2, ensure_ascii=False)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return path
