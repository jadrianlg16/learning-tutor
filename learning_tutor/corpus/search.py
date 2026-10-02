"""Hybrid search: FTS5 bm25 + cosine over embeddings, fused with reciprocal rank.

The shape: FTS candidate ids, semantic results, a window score, and a reciprocal-rank-fusion
loop:

* lexical candidates come from FTS5 ordered by ``bm25()``, with prefix terms joined by OR so
  a multi-word question still finds something;
* semantic candidates come from cosine over stored vectors;
* the two ranked lists fuse with ``1 / (K + rank)``, K = 60, so neither ranker's raw score
  scale has to be calibrated against the other;
* when FTS is empty or unavailable, a coverage-then-density window score over the chunk text
  is the lexical fallback — the same trade ``_window_score`` makes.

Nothing here needs a network when ``LT_EMBED_MODEL=hash``.
"""

from __future__ import annotations

import re
import sqlite3
from typing import Any

from . import embed as embed_mod
from . import roles
from .store import CorpusStore, chunk_row, list_chunks

RRF_RANK_CONSTANT = 60
DEFAULT_K = 8
CANDIDATE_MULTIPLIER = 5
MIN_CANDIDATES = 25
MAX_CANDIDATES = 400

_WORD = re.compile(r"[a-z0-9]+")

#: Words too common to carry retrieval signal or to count towards claim support.
STOPWORDS = frozenset(
    """
    a an the and or but if then than that this these those of in on at to for from by with
    without into over under is are was were be been being am do does did doing have has had
    having it its as not no nor so such can could may might must shall should will would we
    you your they them their he she his her i me my our us any all each every some only also
    when where which who whom whose what how why about between during above below up down out
    off again further once here there both few more most other same too very just because
    """.split()
)


def words(text: str) -> list[str]:
    return _WORD.findall((text or "").lower())


def content_terms(text: str) -> list[str]:
    """Query words worth matching on: no stopwords, at least two characters."""

    seen: list[str] = []
    for word in words(text):
        if len(word) > 1 and word not in STOPWORDS and word not in seen:
            seen.append(word)
    return seen


def _fts_query(terms: list[str]) -> str:
    """OR of prefix terms. AND returns nothing for a realistic multi-word question."""

    return " OR ".join(f'"{term}"*' for term in terms)


def _fts_candidates(
    store: CorpusStore,
    goal_id: str,
    terms: list[str],
    limit: int,
    *,
    role: str | None,
    source_id: str | None,
) -> tuple[list[dict[str, Any]], bool]:
    if not terms:
        return [], False
    sql = """
        SELECT c.*, s.goal_id, s.role, s.kind, s.title, s.path_or_url,
               bm25(chunks_fts) AS bm25
        FROM chunks_fts
        JOIN chunks c ON c.rowid = chunks_fts.rowid
        JOIN sources s ON s.source_id = c.source_id
        WHERE chunks_fts MATCH ? AND s.goal_id = ?
    """
    params: list[Any] = [_fts_query(terms), goal_id]
    if role:
        sql += " AND s.role = ?"
        params.append(role)
    if source_id:
        sql += " AND c.source_id = ?"
        params.append(source_id)
    sql += " ORDER BY bm25(chunks_fts) LIMIT ?"
    params.append(limit)
    try:
        rows = store.query(sql, params)
    except sqlite3.Error:
        return [], False
    return [chunk_row(row) for row in rows], True


def window_score(text: str, terms: list[str], phrase: str) -> float:
    """Coverage first, then density.

    Not all-terms-or-nothing: a 400-token chunk rarely contains every word of a question.
    """

    normalized = (text or "").lower()
    if not normalized:
        return 0.0
    matched = [term for term in terms if term in normalized]
    if not matched and phrase not in normalized:
        return 0.0
    coverage = len(matched) / len(terms) if terms else 0.0
    density = sum(normalized.count(term) for term in matched)
    return coverage * 10 + density + (5 if phrase and phrase in normalized else 0)


def _lexical_fallback(
    store: CorpusStore,
    goal_id: str,
    query: str,
    terms: list[str],
    limit: int,
    *,
    role: str | None,
    source_id: str | None,
) -> list[dict[str, Any]]:
    phrase = " ".join(words(query))
    scored: list[tuple[float, dict[str, Any]]] = []
    for chunk in list_chunks(store, goal_id, role=role, source_id=source_id):
        score = window_score(chunk["text"], terms, phrase)
        if score > 0:
            scored.append((score, chunk))
    scored.sort(key=lambda pair: (-pair[0], pair[1]["chunk_id"]))
    return [chunk for _, chunk in scored[:limit]]


def _semantic_candidates(
    store: CorpusStore,
    goal_id: str,
    query: str,
    limit: int,
    *,
    role: str | None,
    source_id: str | None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    diagnostics: dict[str, Any] = {"used": False, **embed_mod.backend_info()}
    sql = """
        SELECT c.*, s.goal_id, s.role, s.kind, s.title, s.path_or_url, e.vec, e.dim
        FROM embeddings e
        JOIN chunks c ON c.chunk_id = e.chunk_id
        JOIN sources s ON s.source_id = c.source_id
        WHERE s.goal_id = ? AND e.model = ?
    """
    params: list[Any] = [goal_id, embed_mod.model_name()]
    if role:
        sql += " AND s.role = ?"
        params.append(role)
    if source_id:
        sql += " AND c.source_id = ?"
        params.append(source_id)
    rows = store.query(sql, params)
    if not rows:
        diagnostics["reason"] = "no embeddings stored for this goal and model"
        return [], diagnostics

    try:
        query_vec = embed_mod.embed_text(query)
    except embed_mod.EmbeddingError as exc:
        diagnostics["reason"] = str(exc)
        return [], diagnostics

    scored: list[tuple[float, dict[str, Any]]] = []
    for row in rows:
        vector = embed_mod.from_blob(bytes(row["vec"]))
        similarity = embed_mod.cosine(query_vec, vector)
        chunk = chunk_row(row)
        chunk.pop("vec", None)
        chunk.pop("dim", None)
        chunk["semantic_score"] = similarity
        scored.append((similarity, chunk))
    scored.sort(key=lambda pair: (-pair[0], pair[1]["chunk_id"]))
    diagnostics["used"] = True
    diagnostics["scanned"] = len(rows)
    return [chunk for _, chunk in scored[:limit]], diagnostics


def support(query: str, text: str) -> float:
    """Fraction of the query's content terms present in the chunk. 0..1.

    This is the number ``cite`` thresholds on: RRF ranks, it does not measure whether a
    chunk actually says anything about the claim.
    """

    terms = content_terms(query)
    if not terms:
        return 0.0
    haystack = " ".join(words(text))
    hits = sum(1 for term in terms if term in haystack)
    return hits / len(terms)


def search(
    store: CorpusStore,
    goal_id: str,
    query: str,
    *,
    k: int = DEFAULT_K,
    role: str | None = None,
    source_id: str | None = None,
    with_diagnostics: bool = False,
) -> list[dict[str, Any]] | tuple[list[dict[str, Any]], dict[str, Any]]:
    """Hybrid search over one goal's corpus. Filters: ``role``, ``source_id``."""

    if role:
        roles.check_role(role)
    k = max(1, min(int(k or DEFAULT_K), 100))
    terms = content_terms(query)
    candidate_limit = min(max(k * CANDIDATE_MULTIPLIER, MIN_CANDIDATES), MAX_CANDIDATES)

    lexical, fts_used = _fts_candidates(
        store, goal_id, terms, candidate_limit, role=role, source_id=source_id
    )
    if not lexical:
        lexical = _lexical_fallback(
            store, goal_id, query, terms, candidate_limit, role=role, source_id=source_id
        )
        fts_used = False
    semantic, semantic_diag = _semantic_candidates(
        store, goal_id, query, candidate_limit, role=role, source_id=source_id
    )

    lexical_rank = {chunk["chunk_id"]: rank for rank, chunk in enumerate(lexical, start=1)}
    semantic_rank = {chunk["chunk_id"]: rank for rank, chunk in enumerate(semantic, start=1)}
    by_id: dict[str, dict[str, Any]] = {}
    for chunk in semantic:
        by_id[chunk["chunk_id"]] = chunk
    for chunk in lexical:
        merged = by_id.get(chunk["chunk_id"])
        if merged:
            merged.update({key: value for key, value in chunk.items() if key != "semantic_score"})
        else:
            by_id[chunk["chunk_id"]] = chunk

    fused: list[dict[str, Any]] = []
    for chunk_id, chunk in by_id.items():
        score = 0.0
        if chunk_id in lexical_rank:
            score += 1 / (RRF_RANK_CONSTANT + lexical_rank[chunk_id])
        if chunk_id in semantic_rank:
            score += 1 / (RRF_RANK_CONSTANT + semantic_rank[chunk_id])
        chunk = dict(chunk)
        chunk.pop("bm25", None)
        chunk.update(
            {
                "score": score,
                "lexical_rank": lexical_rank.get(chunk_id),
                "semantic_rank": semantic_rank.get(chunk_id),
                "support": support(query, chunk.get("text", "")),
                "proves": roles.proves(str(chunk.get("role") or "")),
            }
        )
        fused.append(chunk)

    fused.sort(
        key=lambda chunk: (
            -float(chunk["score"]),
            -float(chunk["support"]),
            chunk["chunk_id"],
        )
    )
    results = fused[:k]
    if not with_diagnostics:
        return results
    diagnostics = {
        "method": "hybrid_rrf" if semantic_diag.get("used") and lexical else "single_ranker",
        "lexical_method": "sqlite_fts" if fts_used else "in_memory_window",
        "lexical_result_count": len(lexical),
        "semantic": semantic_diag,
        "rrf_k": RRF_RANK_CONSTANT,
        "terms": terms,
    }
    return results, diagnostics
