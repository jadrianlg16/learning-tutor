"""Ingest: document → blocks → chunks → FTS + embeddings + structure.

Extraction is per-format and always produces the same two things:

* **blocks** — ``(locator, text)`` pairs at the format's natural granularity: a PDF page, a
  PPTX slide, a run of DOCX paragraphs under one heading, a Markdown section.
* **structure** — the document's own outline (chapters / slides / headings). The plan phase
  uses it as a prior for the concept graph, so a slide deck teaches in the deck's order.

Chunking never crosses a locator boundary: blocks are grouped by *identical* locator, then
windowed at ~400 tokens with 60 tokens of overlap. That keeps "slide 3" citable as slide 3.

Audio, YouTube and OCR are **adapters**, not pipelines. They call services that already exist
in the ecosystem, addressed only by environment variable, and say so clearly when unset.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from pathlib import Path
from typing import Any

from . import embed as embed_mod
from . import roles, sanitize
from .store import (
    CorpusError,
    CorpusStore,
    get_source,
    goal_sources_dir,
    source_by_sha,
    utcnow,
)

TARGET_TOKENS = 400
OVERLAP_TOKENS = 60

#: A PDF page with less text than this is almost certainly a scan or an equation image.
NEEDS_OCR_CHARS = 20

EXT_KIND = {
    ".pdf": "pdf",
    ".docx": "docx",
    ".pptx": "pptx",
    ".md": "md",
    ".markdown": "md",
    ".txt": "txt",
    ".text": "txt",
    ".mp3": "audio",
    ".wav": "audio",
    ".m4a": "audio",
    ".ogg": "audio",
    ".flac": "audio",
    ".mp4": "audio",
}

Block = tuple[dict[str, Any], str]
Outline = list[dict[str, Any]]


# --- ids and hashes ------------------------------------------------------


def _sid() -> str:
    return f"s_{uuid.uuid4().hex[:12]}"


def _cid(source_id: str, ordinal: int) -> str:
    return f"c_{source_id[2:]}_{ordinal:05d}"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def kind_for(path: Path) -> str:
    kind = EXT_KIND.get(path.suffix.lower())
    if not kind:
        raise CorpusError(
            f"unsupported file type {path.suffix!r}; supported: "
            + ", ".join(sorted(set(EXT_KIND.values())))
        )
    return kind


# --- chunking ------------------------------------------------------------


def estimate_tokens(text: str) -> int:
    return sanitize.estimate_tokens(text)


def _window_words(
    words: list[str], target: int, overlap: int
) -> list[list[str]]:
    """Fixed-size word windows with overlap. Word count ≈ tokens / 1.3."""

    size = max(1, int(target / 1.3))
    step = max(1, size - int(overlap / 1.3))
    if len(words) <= size:
        return [words]
    windows: list[list[str]] = []
    start = 0
    while start < len(words):
        windows.append(words[start : start + size])
        if start + size >= len(words):
            break
        start += step
    return windows


def chunk_blocks(
    blocks: list[Block],
    *,
    target_tokens: int = TARGET_TOKENS,
    overlap_tokens: int = OVERLAP_TOKENS,
) -> list[dict[str, Any]]:
    """Blocks → chunks. Consecutive blocks merge only when their locators are identical."""

    groups: list[tuple[dict[str, Any], list[str]]] = []
    for locator, text in blocks:
        text = (text or "").strip()
        if not text:
            continue
        key = json.dumps(locator, sort_keys=True)
        if groups and json.dumps(groups[-1][0], sort_keys=True) == key:
            groups[-1][1].append(text)
        else:
            groups.append((locator, [text]))

    chunks: list[dict[str, Any]] = []
    for locator, parts in groups:
        joined = "\n".join(parts)
        for window in _window_words(joined.split(), target_tokens, overlap_tokens):
            piece = " ".join(window).strip()
            if not piece:
                continue
            chunks.append(
                {
                    "locator": locator,
                    "text": piece,
                    "tokens_est": estimate_tokens(piece),
                }
            )
    return chunks


# --- extractors ----------------------------------------------------------


def extract_pdf(path: Path) -> tuple[list[Block], Outline, dict[str, Any]]:
    try:
        import fitz  # PyMuPDF
    except ImportError as exc:  # pragma: no cover - depends on install extras
        raise CorpusError("PDF ingest needs PyMuPDF (pip install pymupdf)") from exc

    blocks: list[Block] = []
    outline: Outline = []
    needs_ocr: list[int] = []
    with fitz.open(path) as doc:
        for entry in doc.get_toc() or []:
            level, title, page = entry[0], entry[1], entry[2]
            outline.append(
                {
                    "level": max(1, int(level)),
                    "title": str(title).strip(),
                    "locator": {"page": int(page)},
                }
            )
        for index, page in enumerate(doc, start=1):
            text = (page.get_text() or "").strip()
            if len(text) < NEEDS_OCR_CHARS:
                needs_ocr.append(index)
                if not text:
                    continue
            blocks.append(({"page": index}, text))
        page_count = doc.page_count

    if not outline:
        # No embedded TOC: the page sequence is the only outline the document offers.
        outline = [
            {"level": 1, "title": f"Page {locator['page']}", "locator": locator}
            for locator, _ in blocks
        ]
    meta: dict[str, Any] = {"pages": page_count}
    if needs_ocr:
        meta["needs_ocr"] = True
        meta["needs_ocr_pages"] = needs_ocr
    return blocks, outline, meta


def extract_docx(path: Path) -> tuple[list[Block], Outline, dict[str, Any]]:
    try:
        import docx  # python-docx
    except ImportError as exc:  # pragma: no cover
        raise CorpusError("DOCX ingest needs python-docx (pip install python-docx)") from exc

    document = docx.Document(str(path))
    blocks: list[Block] = []
    outline: Outline = []
    current = "(document)"
    for paragraph in document.paragraphs:
        text = (paragraph.text or "").strip()
        if not text:
            continue
        level = _docx_heading_level(paragraph)
        if level:
            current = text
            outline.append({"level": level, "title": text, "locator": {"heading": text}})
            continue
        blocks.append(({"heading": current}, text))
    if not outline:
        outline = [{"level": 1, "title": path.stem, "locator": {"heading": "(document)"}}]
    return blocks, outline, {"paragraphs": len(document.paragraphs)}


def _docx_heading_level(paragraph: Any) -> int | None:
    style = getattr(getattr(paragraph, "style", None), "name", "") or ""
    match = re.match(r"^Heading (\d+)$", style.strip())
    if match:
        return int(match.group(1))
    if style.strip() in {"Title", "Subtitle"}:
        return 1
    return None


def extract_pptx(path: Path) -> tuple[list[Block], Outline, dict[str, Any]]:
    try:
        from pptx import Presentation  # python-pptx
    except ImportError as exc:  # pragma: no cover
        raise CorpusError("PPTX ingest needs python-pptx (pip install python-pptx)") from exc

    presentation = Presentation(str(path))
    blocks: list[Block] = []
    outline: Outline = []
    slide_count = 0
    for index, slide in enumerate(presentation.slides, start=1):
        slide_count = index
        locator = {"slide": index}
        title = ""
        try:
            if slide.shapes.title is not None:
                title = (slide.shapes.title.text or "").strip()
        except AttributeError:  # pragma: no cover - layouts without a title placeholder
            title = ""
        pieces: list[str] = []
        for shape in slide.shapes:
            if getattr(shape, "has_text_frame", False):
                text = (shape.text_frame.text or "").strip()
                if text:
                    pieces.append(text)
        notes = ""
        if getattr(slide, "has_notes_slide", False):
            notes = (slide.notes_slide.notes_text_frame.text or "").strip()
        if notes:
            pieces.append(f"[speaker notes] {notes}")
        outline.append(
            {"level": 1, "title": title or f"Slide {index}", "locator": locator}
        )
        if pieces:
            blocks.append((locator, "\n".join(pieces)))
    return blocks, outline, {"slides": slide_count}


_MD_HEADING = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")


def extract_markdown(text: str, *, title: str) -> tuple[list[Block], Outline, dict[str, Any]]:
    blocks: list[Block] = []
    outline: Outline = []
    current = "(document)"
    buffer: list[str] = []

    def flush() -> None:
        body = "\n".join(buffer).strip()
        if body:
            blocks.append(({"heading": current}, body))
        buffer.clear()

    for line in text.splitlines():
        match = _MD_HEADING.match(line)
        if match:
            flush()
            current = match.group(2).strip()
            outline.append(
                {
                    "level": len(match.group(1)),
                    "title": current,
                    "locator": {"heading": current},
                }
            )
        else:
            buffer.append(line)
    flush()
    if not outline:
        outline = [{"level": 1, "title": title, "locator": {"heading": "(document)"}}]
    return blocks, outline, {"headings": len(outline)}


def extract_plaintext(text: str, *, title: str) -> tuple[list[Block], Outline, dict[str, Any]]:
    """TXT with no heading syntax: paragraphs are the locator granularity."""

    if _MD_HEADING.search(text):
        return extract_markdown(text, title=title)
    blocks: list[Block] = []
    for index, para in enumerate(re.split(r"\n\s*\n", text), start=1):
        body = para.strip()
        if body:
            blocks.append(({"para": index}, body))
    outline = [{"level": 1, "title": title, "locator": {"heading": "(document)"}}]
    return blocks, outline, {"paragraphs": len(blocks)}


# --- optional service adapters -------------------------------------------


def _service_url(env_name: str, what: str) -> str:
    url = os.environ.get(env_name)
    if not url:
        raise CorpusError(
            f"{what} service not configured: set {env_name} to its base URL "
            f"(no host is hardcoded anywhere in this module)"
        )
    return url.rstrip("/")


def _post_json(url: str, payload: dict[str, Any], *, timeout: float = 300.0) -> dict[str, Any]:
    try:
        import httpx
    except ImportError as exc:  # pragma: no cover
        raise CorpusError("service adapters need httpx (pip install httpx)") from exc
    try:
        with httpx.Client(timeout=timeout) as client:
            response = client.post(url, json=payload)
            response.raise_for_status()
            return response.json()
    except Exception as exc:
        raise CorpusError(f"request to {url} failed: {exc}") from exc


def _segments_to_blocks(segments: list[dict[str, Any]]) -> list[Block]:
    blocks: list[Block] = []
    for segment in segments:
        text = str(segment.get("text") or "").strip()
        if not text:
            continue
        blocks.append(({"t": _timestamp(segment.get("start"))}, text))
    return blocks


def _timestamp(start: Any) -> str:
    try:
        total = int(float(start or 0))
    except (TypeError, ValueError):
        total = 0
    return f"{total // 3600:02d}:{(total % 3600) // 60:02d}:{total % 60:02d}"


def transcribe_audio(path: Path) -> tuple[list[Block], Outline, dict[str, Any]]:
    """Audio transcription adapter: POSTs the path to the service at ``LT_TRANSCRIBE_URL``."""

    base = _service_url("LT_TRANSCRIBE_URL", "audio transcription")
    payload = _post_json(f"{base}/transcribe", {"path": str(path)})
    segments = payload.get("segments") or []
    blocks = _segments_to_blocks(segments)
    if not blocks and payload.get("text"):
        blocks = [({"t": "00:00:00"}, str(payload["text"]))]
    outline = [{"level": 1, "title": path.stem, "locator": {"t": "00:00:00"}}]
    return blocks, outline, {"service": base, "segments": len(segments)}


def fetch_youtube(url: str) -> tuple[list[Block], Outline, dict[str, Any]]:
    """YouTube transcript adapter: POSTs the URL to the service at ``LT_YT_TRANSCRIPTS_URL``."""

    base = _service_url("LT_YT_TRANSCRIPTS_URL", "YouTube transcript")
    payload = _post_json(f"{base}/transcript", {"url": url})
    segments = payload.get("segments") or []
    blocks = _segments_to_blocks(segments)
    if not blocks and payload.get("text"):
        blocks = [({"t": "00:00:00"}, str(payload["text"]))]
    title = str(payload.get("title") or url)
    outline = [{"level": 1, "title": title, "locator": {"t": "00:00:00"}}]
    return blocks, outline, {"service": base, "segments": len(segments), "title": title}


def ocr_pages(path: Path, pages: list[int]) -> list[Block]:
    """OCR adapter: POSTs the path and page numbers to the service at ``LT_OCR_URL``."""

    base = _service_url("LT_OCR_URL", "OCR")
    payload = _post_json(f"{base}/ocr", {"path": str(path), "pages": pages})
    blocks: list[Block] = []
    for entry in payload.get("pages") or []:
        text = str(entry.get("text") or "").strip()
        page = entry.get("page")
        if text and page is not None:
            blocks.append(({"page": int(page)}, text))
    return blocks


# --- writing -------------------------------------------------------------


def _write_source(
    store: CorpusStore,
    *,
    goal_id: str,
    role: str,
    kind: str,
    title: str,
    path_or_url: str,
    sha256: str,
    meta: dict[str, Any],
    blocks: list[Block],
    outline: Outline,
    embed_model: str | None,
) -> dict[str, Any]:
    source_id = _sid()
    chunks = chunk_blocks(blocks)
    flagged = 0

    store.execute("BEGIN")
    try:
        store.insert(
            "sources",
            {
                "source_id": source_id,
                "goal_id": goal_id,
                "role": role,
                "kind": kind,
                "title": title,
                "path_or_url": path_or_url,
                "sha256": sha256,
                "ingested_at": utcnow(),
                "meta": json.dumps(meta, ensure_ascii=False),
            },
        )
        for ordinal, entry in enumerate(outline):
            store.insert(
                "structure",
                {
                    "source_id": source_id,
                    "ordinal": ordinal,
                    "level": int(entry.get("level") or 1),
                    "title": str(entry.get("title") or ""),
                    "locator": json.dumps(entry.get("locator") or {}, sort_keys=True),
                },
            )
        for ordinal, chunk in enumerate(chunks):
            findings = sanitize.scan(chunk["text"])
            if findings:
                flagged += 1
            chunk["chunk_id"] = _cid(source_id, ordinal)
            store.insert(
                "chunks",
                {
                    "chunk_id": chunk["chunk_id"],
                    "source_id": source_id,
                    "ordinal": ordinal,
                    "locator": json.dumps(chunk["locator"], sort_keys=True),
                    "text": chunk["text"],
                    "tokens_est": chunk["tokens_est"],
                    "flags": json.dumps(findings, ensure_ascii=False),
                },
            )
        store.execute("COMMIT")
    except Exception:
        store.execute("ROLLBACK")
        raise

    embedded, embed_error = embed_source(store, source_id, model=embed_model)
    result = {
        "status": "ingested",
        "source_id": source_id,
        "goal_id": goal_id,
        "role": role,
        "kind": kind,
        "title": title,
        "sha256": sha256,
        "chunks": len(chunks),
        "structure": len(outline),
        "flagged_chunks": flagged,
        "embedded": embedded,
        "meta": meta,
    }
    if embed_error:
        result["embed_error"] = embed_error
    return result


def embed_source(
    store: CorpusStore, source_id: str, *, model: str | None = None
) -> tuple[int, str | None]:
    """Embed every chunk of a source. A failure degrades search to FTS, never blocks ingest."""

    model = model or embed_mod.model_name()
    rows = store.query(
        "SELECT chunk_id, text FROM chunks WHERE source_id = ? ORDER BY ordinal",
        (source_id,),
    )
    if not rows:
        return 0, None
    texts = [str(row["text"]) for row in rows]
    try:
        vectors = embed_mod.embed_texts(texts, model=model)
    except embed_mod.EmbeddingError as exc:
        return 0, str(exc)

    store.execute("BEGIN")
    try:
        for row, vector in zip(rows, vectors, strict=False):
            store.execute(
                "INSERT OR REPLACE INTO embeddings(chunk_id, model, dim, vec) VALUES (?, ?, ?, ?)",
                (str(row["chunk_id"]), model, len(vector), embed_mod.to_blob(vector)),
            )
        store.execute("COMMIT")
    except Exception:
        store.execute("ROLLBACK")
        raise
    return len(rows), None


# --- public entry points -------------------------------------------------


def resolve_path(goal_id: str, path: str, settings: Any = None) -> Path:
    """Accept an absolute path or one relative to ``LT_DATA_DIR/sources/<goal>/``.

    A relative path may not escape the goal's source directory.
    """

    base = goal_sources_dir(goal_id, settings)
    base.mkdir(parents=True, exist_ok=True)
    base = base.resolve()
    candidate = Path(path).expanduser()
    if candidate.is_absolute():
        resolved = candidate.resolve()
    else:
        resolved = (base / candidate).resolve()
        if base not in resolved.parents and resolved != base:
            raise CorpusError(f"path {path!r} escapes the goal source directory")
    if not resolved.exists():
        raise CorpusError(f"file not found: {resolved}")
    return resolved


def ingest_file(
    store: CorpusStore,
    goal_id: str,
    path: str | Path,
    *,
    role: str,
    kind: str | None = None,
    title: str | None = None,
    meta: dict[str, Any] | None = None,
    embed_model: str | None = None,
) -> dict[str, Any]:
    roles.check_role(role)
    resolved = path if isinstance(path, Path) else resolve_path(goal_id, str(path), store.settings)
    resolved = Path(resolved)
    if not resolved.exists():
        raise CorpusError(f"file not found: {resolved}")

    kind = roles.check_kind(kind) if kind else kind_for(resolved)
    digest = sha256_file(resolved)
    existing = source_by_sha(store, goal_id, digest)
    if existing:
        return {
            "status": "duplicate",
            "source_id": existing["source_id"],
            "goal_id": goal_id,
            "role": existing["role"],
            "kind": existing["kind"],
            "title": existing["title"],
            "sha256": digest,
            "reason": "a source with this sha256 is already ingested for this goal",
        }

    if kind == "pdf":
        blocks, outline, extra = extract_pdf(resolved)
        if extra.get("needs_ocr_pages") and os.environ.get("LT_OCR_URL"):
            blocks = sorted(
                blocks + ocr_pages(resolved, list(extra["needs_ocr_pages"])),
                key=lambda item: int(item[0].get("page") or 0),
            )
            extra["ocr_applied"] = True
    elif kind == "docx":
        blocks, outline, extra = extract_docx(resolved)
    elif kind == "pptx":
        blocks, outline, extra = extract_pptx(resolved)
    elif kind == "md":
        blocks, outline, extra = extract_markdown(
            resolved.read_text(encoding="utf-8", errors="replace"), title=resolved.stem
        )
    elif kind == "txt":
        blocks, outline, extra = extract_plaintext(
            resolved.read_text(encoding="utf-8", errors="replace"), title=resolved.stem
        )
    elif kind == "audio":
        blocks, outline, extra = transcribe_audio(resolved)
    else:
        raise CorpusError(f"kind {kind!r} is not a file kind; use ingest_url for it")

    merged = {**extra, **(meta or {}), "source_bytes": resolved.stat().st_size}
    return _write_source(
        store,
        goal_id=goal_id,
        role=role,
        kind=kind,
        title=title or resolved.stem,
        path_or_url=str(resolved),
        sha256=digest,
        meta=merged,
        blocks=blocks,
        outline=outline,
        embed_model=embed_model,
    )


def ingest_text(
    store: CorpusStore,
    goal_id: str,
    text: str,
    *,
    role: str,
    title: str,
    kind: str = "md",
    path_or_url: str = "",
    meta: dict[str, Any] | None = None,
    embed_model: str | None = None,
) -> dict[str, Any]:
    """In-memory ingest. Used by the URL/YouTube adapters and by tests."""

    roles.check_role(role)
    roles.check_kind(kind)
    digest = sha256_text(text)
    existing = source_by_sha(store, goal_id, digest)
    if existing:
        return {
            "status": "duplicate",
            "source_id": existing["source_id"],
            "goal_id": goal_id,
            "role": existing["role"],
            "kind": existing["kind"],
            "title": existing["title"],
            "sha256": digest,
            "reason": "a source with this sha256 is already ingested for this goal",
        }
    if kind == "md":
        blocks, outline, extra = extract_markdown(text, title=title)
    else:
        blocks, outline, extra = extract_plaintext(text, title=title)
    return _write_source(
        store,
        goal_id=goal_id,
        role=role,
        kind=kind,
        title=title,
        path_or_url=path_or_url or f"memory:{digest[:12]}",
        sha256=digest,
        meta={**extra, **(meta or {})},
        blocks=blocks,
        outline=outline,
        embed_model=embed_model,
    )


def ingest_youtube(
    store: CorpusStore,
    goal_id: str,
    url: str,
    *,
    role: str,
    title: str | None = None,
    embed_model: str | None = None,
) -> dict[str, Any]:
    roles.check_role(role)
    blocks, outline, extra = fetch_youtube(url)
    digest = sha256_text(url + "\n" + "\n".join(text for _, text in blocks))
    existing = source_by_sha(store, goal_id, digest)
    if existing:
        return {
            "status": "duplicate",
            "source_id": existing["source_id"],
            "goal_id": goal_id,
            "sha256": digest,
            "reason": "this transcript is already ingested for this goal",
        }
    return _write_source(
        store,
        goal_id=goal_id,
        role=role,
        kind="youtube",
        title=title or str(extra.get("title") or url),
        path_or_url=url,
        sha256=digest,
        meta=extra,
        blocks=blocks,
        outline=outline,
        embed_model=embed_model,
    )


def reindex_source(
    store: CorpusStore, source_id: str, *, model: str | None = None
) -> dict[str, Any]:
    source = get_source(store, source_id)
    if not source:
        raise CorpusError(f"unknown source {source_id!r}")
    embedded, error = embed_source(store, source_id, model=model)
    out = {"source_id": source_id, "embedded": embedded}
    if error:
        out["embed_error"] = error
    return out
