"""Cite or abstain, and flag alignment/authority conflicts instead of resolving them.

Two rules from IDEA.md drive this file:

* *Citing a slide proves alignment, not truth.* Every citation carries ``proves``:
  ``alignment`` for an alignment source, ``correctness`` for an authority source,
  ``learner_evidence`` for the learner's own notes.
* *Conflicts are flagged to the learner, never silently resolved.* :func:`conflicts` returns
  both readings, labelled exam-mode and truth-mode.

Abstention is on purpose: a claim the corpus does not support returns ``abstain`` with a
reason, and the caller must say so rather than assert the claim with a decorative citation.
"""

from __future__ import annotations

import os
import re
from typing import Any

from . import roles
from .search import DEFAULT_K, content_terms, search, support, words
from .store import CorpusStore

QUOTE_LIMIT = 200
DEFAULT_MIN_SCORE = 0.5

_SENTENCE = re.compile(r"(?<=[.!?])\s+|\n+")
_NUMBER = re.compile(r"[-+]?\d+(?:[.,]\d+)?")
_NEGATION = frozenset(
    """
    not no never none cannot cant can't isn't isnt aren't arent doesn't doesnt don't dont
    won't wont without neither nor fails unless except false incorrect wrong
    """.split()
)


def min_score_default() -> float:
    raw = os.environ.get("LT_CITE_MIN_SCORE")
    if not raw:
        return DEFAULT_MIN_SCORE
    try:
        return float(raw)
    except ValueError:
        return DEFAULT_MIN_SCORE


def sentences(text: str) -> list[str]:
    return [part.strip() for part in _SENTENCE.split(text or "") if part.strip()]


def best_quote(text: str, claim: str, limit: int = QUOTE_LIMIT) -> str:
    """The sentence of the chunk that best supports the claim, capped at ``limit`` chars."""

    terms = content_terms(claim)
    parts = sentences(text) or [text or ""]
    best = parts[0]
    best_hits = -1.0
    for part in parts:
        haystack = " ".join(words(part))
        hits = sum(1 for term in terms if term in haystack)
        # Prefer a sentence that covers more terms; break ties towards the shorter one.
        score = hits - len(part) / 10000.0
        if score > best_hits:
            best_hits = score
            best = part
    quote = " ".join(best.split())
    if len(quote) > limit:
        quote = quote[: limit - 1].rstrip() + "…"
    return quote


def _citation(chunk: dict[str, Any], claim: str) -> dict[str, Any]:
    role = str(chunk.get("role") or "")
    return {
        "source_id": chunk.get("source_id"),
        "chunk_id": chunk.get("chunk_id"),
        "title": chunk.get("title"),
        "locator": chunk.get("locator") or {},
        "quote": best_quote(str(chunk.get("text") or ""), claim),
        "role": role,
        "proves": roles.proves(role),
        "score": round(float(chunk.get("score") or 0.0), 6),
        "support": round(float(chunk.get("support") or 0.0), 4),
        "flagged": bool(chunk.get("flags")),
    }


def cite_or_abstain(
    store: CorpusStore,
    claim: str,
    goal_id: str,
    *,
    k: int = 5,
    min_score: float | None = None,
    role: str | None = None,
) -> dict[str, Any]:
    """Cite the corpus for ``claim`` or abstain.

    ``min_score`` thresholds ``support`` — the fraction of the claim's content words that
    appear in the chunk — not the RRF score, whose scale means nothing on its own. Default
    0.5 (``LT_CITE_MIN_SCORE``): at least half the claim's content words must be there.
    """

    threshold = min_score_default() if min_score is None else float(min_score)
    claim = (claim or "").strip()
    if not claim:
        return {"status": "abstain", "reason": "empty claim", "claim": claim}
    if not content_terms(claim):
        return {
            "status": "abstain",
            "reason": "claim has no content words to match against the corpus",
            "claim": claim,
        }

    hits = search(store, goal_id, claim, k=max(k, DEFAULT_K), role=role)
    assert isinstance(hits, list)
    if not hits:
        return {
            "status": "abstain",
            "reason": "no corpus material matched this claim",
            "claim": claim,
            "min_score": threshold,
        }

    supported = [chunk for chunk in hits if float(chunk.get("support") or 0.0) >= threshold]
    if not supported:
        best = max(float(chunk.get("support") or 0.0) for chunk in hits)
        return {
            "status": "abstain",
            "reason": (
                f"best corpus match supports only {best:.2f} of the claim's content words "
                f"(threshold {threshold:.2f})"
            ),
            "claim": claim,
            "min_score": threshold,
            "best_support": round(best, 4),
        }

    citations = [_citation(chunk, claim) for chunk in supported[:k]]
    return {
        "status": "cited",
        "claim": claim,
        "min_score": threshold,
        "citations": citations,
        "proves": sorted({citation["proves"] for citation in citations}),
    }


# --- conflicts -----------------------------------------------------------


def _numbers(text: str) -> set[str]:
    out: set[str] = set()
    for raw in _NUMBER.findall(text or ""):
        value = raw.replace(",", ".")
        try:
            number = float(value)
        except ValueError:
            continue
        # Normalise 1 and 1.0 to the same token so "1" vs "-1" is a difference but
        # "1" vs "1.0" is not.
        out.add(f"{number:g}")
    return out


def _negated(text: str) -> bool:
    return any(word in _NEGATION for word in words(text))


def _relevant_sentences(text: str, shared: set[str]) -> list[str]:
    picked = []
    for sentence in sentences(text):
        haystack = set(words(sentence))
        if haystack & shared:
            picked.append(sentence)
    return picked or sentences(text)[:1]


def disagreement(text_a: str, text_b: str, topic: str) -> dict[str, Any]:
    """The conflict heuristic, in one place so the docs can quote it.

    1. The two texts must be *about the same thing*: at least two shared content words,
       counting the topic's own words.
    2. Restrict each text to the sentences that mention a shared word.
    3. **Negation** — one side negates and the other does not.
    4. **Numeric** — both sides state numbers and the number sets differ.

    HYPOTHESIS: that this catches the conflicts that matter on real course material. It is
    a lexical test with no model in the loop, so it will miss paraphrased disagreement and
    will fire on two sources that simply discuss different cases. It flags; it never
    resolves, and the learner sees both readings.
    """

    terms_a = set(content_terms(text_a))
    terms_b = set(content_terms(text_b))
    shared = (terms_a & terms_b) | (set(content_terms(topic)) & terms_a & terms_b)
    basis: list[str] = []
    if len(shared) < 2:
        return {"conflict": False, "basis": basis, "shared_terms": sorted(shared)}

    part_a = " ".join(_relevant_sentences(text_a, shared))
    part_b = " ".join(_relevant_sentences(text_b, shared))

    if _negated(part_a) != _negated(part_b):
        basis.append("negation")

    numbers_a = _numbers(part_a)
    numbers_b = _numbers(part_b)
    if numbers_a and numbers_b and numbers_a != numbers_b:
        basis.append("numeric")

    return {
        "conflict": bool(basis),
        "basis": basis,
        "shared_terms": sorted(shared)[:12],
        "numbers": {"alignment": sorted(numbers_a), "authority": sorted(numbers_b)},
    }


def _reading(chunk: dict[str, Any], topic: str, note: str) -> dict[str, Any]:
    role = str(chunk.get("role") or "")
    return {
        "mode": roles.mode(role),
        "role": role,
        "proves": roles.proves(role),
        "source_id": chunk.get("source_id"),
        "chunk_id": chunk.get("chunk_id"),
        "title": chunk.get("title"),
        "locator": chunk.get("locator") or {},
        "quote": best_quote(str(chunk.get("text") or ""), topic),
        "note": note,
    }


EXAM_NOTE = "For your exam, this is what the course material says."
TRUTH_NOTE = "Canonically, the authority source says this."


MIN_TOPIC_SUPPORT = 0.5


def conflicts(
    store: CorpusStore,
    goal_id: str,
    topic: str,
    *,
    k: int = 5,
    min_topic_support: float = MIN_TOPIC_SUPPORT,
) -> dict[str, Any]:
    """Alignment vs authority on one topic. Both readings, labelled; never auto-resolved.

    Pair selection matters as much as the disagreement test. Both sides are first filtered
    to chunks that are actually about the topic (``support`` ≥ ``min_topic_support``), then
    the *single* pair sharing the most vocabulary is the one compared. Comparing every pair
    and reporting the first hit makes any corpus containing one negated sentence look like a
    conflict on every topic.
    """

    topic = (topic or "").strip()
    if not topic:
        return {"status": "insufficient", "reason": "empty topic", "topic": topic}

    alignment = search(store, goal_id, topic, k=k, role=roles.ALIGNMENT)
    authority = search(store, goal_id, topic, k=k, role=roles.AUTHORITY)
    assert isinstance(alignment, list) and isinstance(authority, list)

    on_topic_a = [
        chunk
        for chunk in alignment
        if support(topic, str(chunk.get("text") or "")) >= min_topic_support
    ]
    on_topic_b = [
        chunk
        for chunk in authority
        if support(topic, str(chunk.get("text") or "")) >= min_topic_support
    ]

    if not on_topic_a or not on_topic_b:
        missing = roles.ALIGNMENT if not on_topic_a else roles.AUTHORITY
        return {
            "status": "insufficient",
            "reason": (
                f"no {missing} source is on-topic enough for this comparison "
                f"(support < {min_topic_support:.2f}); a conflict needs both roles"
            ),
            "topic": topic,
            "alignment_hits": len(on_topic_a),
            "authority_hits": len(on_topic_b),
        }

    def shared_size(chunk_a: dict[str, Any], chunk_b: dict[str, Any]) -> int:
        return len(
            set(content_terms(str(chunk_a["text"]))) & set(content_terms(str(chunk_b["text"])))
        )

    pairs = [
        (
            -shared_size(chunk_a, chunk_b),
            -(
                support(topic, str(chunk_a["text"])) + support(topic, str(chunk_b["text"]))
            ),
            str(chunk_a["chunk_id"]),
            str(chunk_b["chunk_id"]),
            chunk_a,
            chunk_b,
        )
        for chunk_a in on_topic_a
        for chunk_b in on_topic_b
    ]
    pairs.sort(key=lambda item: item[:4])
    chunk_a, chunk_b = pairs[0][4], pairs[0][5]
    verdict = disagreement(str(chunk_a["text"]), str(chunk_b["text"]), topic)

    if not verdict["conflict"]:
        return {
            "status": "agree",
            "topic": topic,
            "reason": "no negation or numeric disagreement found between the two roles",
            "shared_terms": verdict["shared_terms"],
            "readings": [
                _reading(chunk_a, topic, EXAM_NOTE),
                _reading(chunk_b, topic, TRUTH_NOTE),
            ],
        }

    return {
        "status": "conflict",
        "topic": topic,
        "basis": verdict["basis"],
        "shared_terms": verdict["shared_terms"],
        "readings": [
            _reading(chunk_a, topic, EXAM_NOTE),
            _reading(chunk_b, topic, TRUTH_NOTE),
        ],
        "resolution": (
            "Not resolved. Show both: the alignment source constrains what the exam expects, "
            "the authority source supports what is correct."
        ),
        "heuristic": "lexical negation / numeric difference over shared terms (HYPOTHESIS)",
    }
