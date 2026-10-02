"""Per-goal ``sources.md``: the learner's standing instructions about *material*.

Lives at ``LT_DATA_DIR/sources/<goal_id>/sources.md`` — next to the documents themselves, so
the folder is the whole story for a goal. It is the input to the research pass and the place
notation and depth preferences are recorded.

It is markdown because a person edits it. The parser is deliberately forgiving: unknown
sections and unknown ``key: value`` lines are preserved in ``extra`` and written back out, so
hand edits survive a round trip.

Note the asymmetry with the rest of this module: ``sources.md`` is written *by the learner*
and is a legitimate instruction source about which material to trust. Document text pulled
into the corpus is not. The two never mix — nothing in this file is rendered through
``sanitize.render_for_context``, and nothing ingested ever lands here.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from ..config import Settings
from .store import goal_sources_dir

FILENAME = "sources.md"

SCALARS = ("notation", "depth", "language", "exam_format", "source_priority")
LISTS = {
    "trusted": "Trusted sources",
    "banned": "Banned sources",
    "preferred_textbooks": "Preferred textbooks",
}

_HEADING = re.compile(r"^#{1,6}\s+(.*\S)\s*$")
_BULLET = re.compile(r"^\s*[-*+]\s+(.*\S)\s*$")
_KV = re.compile(r"^\s*(?:[-*+]\s+)?\*{0,2}([A-Za-z][A-Za-z _-]*?)\*{0,2}\s*:\s*(.*\S)\s*$")

_SECTION_ALIASES = {
    "trusted": "trusted",
    "trusted sources": "trusted",
    "banned": "banned",
    "banned sources": "banned",
    "preferred textbooks": "preferred_textbooks",
    "textbooks": "preferred_textbooks",
    "preferences": "_preferences",
    "notes": "_notes",
}


@dataclass
class SourcesSpec:
    """The parsed ``sources.md`` for one goal."""

    goal_id: str = ""
    notation: str = ""
    depth: str = ""
    language: str = ""
    exam_format: str = ""
    source_priority: str = ""
    trusted: list[str] = field(default_factory=list)
    banned: list[str] = field(default_factory=list)
    preferred_textbooks: list[str] = field(default_factory=list)
    notes: str = ""
    extra: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def is_banned(self, candidate: str) -> bool:
        needle = (candidate or "").lower()
        return any(entry.lower() in needle or needle in entry.lower() for entry in self.banned)


def path_for(goal_id: str, settings: Settings | None = None) -> Path:
    return goal_sources_dir(goal_id, settings) / FILENAME


def _normalise_key(key: str) -> str:
    return key.strip().lower().replace(" ", "_").replace("-", "_")


def parse(text: str, *, goal_id: str = "") -> SourcesSpec:
    spec = SourcesSpec(goal_id=goal_id)
    section: str | None = None
    notes: list[str] = []

    for line in (text or "").splitlines():
        heading = _HEADING.match(line)
        if heading:
            title = heading.group(1).strip()
            lowered = title.lower()
            section = _SECTION_ALIASES.get(lowered)
            if section is None:
                for alias, target in _SECTION_ALIASES.items():
                    if alias in lowered:
                        section = target
                        break
            if section is None and not spec.goal_id and lowered.startswith("sources"):
                section = "_preferences"
            continue

        bullet = _BULLET.match(line)
        payload = bullet.group(1) if bullet else line

        kv = _KV.match(payload if bullet else line)
        if kv and (section in (None, "_preferences") or _normalise_key(kv.group(1)) in SCALARS):
            key = _normalise_key(kv.group(1))
            value = kv.group(2).strip()
            if key in SCALARS:
                setattr(spec, key, value)
            elif key in LISTS:
                getattr(spec, key).extend(
                    part.strip() for part in value.split(";") if part.strip()
                )
            else:
                spec.extra[key] = value
            continue

        if bullet and section in LISTS:
            getattr(spec, section).append(bullet.group(1).strip())
            continue

        if section == "_notes" and line.strip():
            notes.append(line.rstrip())

    spec.notes = "\n".join(notes).strip()
    return spec


def render(spec: SourcesSpec) -> str:
    lines: list[str] = [f"# Sources — {spec.goal_id or 'goal'}", ""]
    lines.append("## Preferences")
    lines.append("")
    for key in SCALARS:
        lines.append(f"- {key}: {getattr(spec, key) or ''}")
    for key, value in sorted(spec.extra.items()):
        lines.append(f"- {key}: {value}")
    lines.append("")
    for attribute, title in LISTS.items():
        lines.append(f"## {title}")
        lines.append("")
        entries = getattr(spec, attribute)
        if entries:
            lines.extend(f"- {entry}" for entry in entries)
        else:
            lines.append("- (none yet)")
        lines.append("")
    lines.append("## Notes")
    lines.append("")
    lines.append(spec.notes or "(none)")
    lines.append("")
    return "\n".join(lines)


def default_spec(goal_id: str) -> SourcesSpec:
    return SourcesSpec(
        goal_id=goal_id,
        notation="use the alignment sources' notation",
        depth="explain",
        language="en",
        exam_format="",
        source_priority="alignment",
    )


def load(goal_id: str, settings: Settings | None = None) -> SourcesSpec:
    """Read the goal's ``sources.md``, or the defaults if it does not exist yet."""

    path = path_for(goal_id, settings)
    if not path.exists():
        return default_spec(goal_id)
    return parse(path.read_text(encoding="utf-8"), goal_id=goal_id)


def save(goal_id: str, spec: SourcesSpec, settings: Settings | None = None) -> Path:
    path = path_for(goal_id, settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    spec.goal_id = spec.goal_id or goal_id
    path.write_text(render(spec), encoding="utf-8")
    return path


def save_text(goal_id: str, text: str, settings: Settings | None = None) -> SourcesSpec:
    """Write raw markdown the learner supplied, then return what the parser makes of it."""

    path = path_for(goal_id, settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return parse(text, goal_id=goal_id)


def update(goal_id: str, changes: dict[str, Any], settings: Settings | None = None) -> SourcesSpec:
    spec = load(goal_id, settings)
    for key, value in (changes or {}).items():
        key = _normalise_key(str(key))
        if key in SCALARS:
            setattr(spec, key, str(value))
        elif key in LISTS:
            items = value if isinstance(value, list) else [value]
            setattr(spec, key, [str(item) for item in items])
        elif key == "notes":
            spec.notes = str(value)
        else:
            spec.extra[key] = str(value)
    save(goal_id, spec, settings)
    return spec
