"""Ingest: real files built in the test, real extraction, real locators.

No fixture binaries are checked in — a 3-page PDF, a 2-heading DOCX and a 3-slide PPTX are
generated here, so what the test proves is that PyMuPDF / python-docx / python-pptx actually
produce the page, heading and slide locators the citation layer depends on.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from learning_tutor.corpus import ingest as ingest_mod
from learning_tutor.corpus import roles
from learning_tutor.corpus.store import (
    CorpusError,
    get_structure,
    goal_sources_dir,
    list_chunks,
    list_sources,
    open_store,
)

GOAL = "g_forms"

PDF_PAGES = [
    "Chapter 1. Vectors\n"
    "A vector is an element of a vector space. Vectors add componentwise and scale by a "
    "field element. The zero vector is the additive identity.",
    "Chapter 2. Covectors\n"
    "A covector is a linear map from the vector space to its field of scalars. Covectors "
    "form the dual space. The dual basis is defined by evaluation on the basis vectors.",
    "fig.",  # under 20 characters: a scanned or equation-only page
]


@pytest.fixture(autouse=True)
def offline_embeddings(monkeypatch):
    """The hashed backend: deterministic, offline, no Ollama in the test path."""

    monkeypatch.setenv("LT_EMBED_MODEL", "hash")
    monkeypatch.delenv("LT_TRANSCRIBE_URL", raising=False)
    monkeypatch.delenv("LT_YT_TRANSCRIPTS_URL", raising=False)
    monkeypatch.delenv("LT_OCR_URL", raising=False)


@pytest.fixture
def corpus(settings):
    with open_store(settings) as store:
        yield store


@pytest.fixture
def source_dir(settings) -> Path:
    path = goal_sources_dir(GOAL, settings)
    path.mkdir(parents=True, exist_ok=True)
    return path


def make_pdf(path: Path, pages: list[str] = PDF_PAGES) -> Path:
    fitz = pytest.importorskip("fitz", reason="PyMuPDF is required for PDF ingest")
    doc = fitz.open()
    for text in pages:
        page = doc.new_page()
        page.insert_textbox(fitz.Rect(50, 50, 545, 750), text, fontsize=11)
    doc.save(str(path))
    doc.close()
    return path


def make_docx(path: Path) -> Path:
    docx = pytest.importorskip("docx", reason="python-docx is required for DOCX ingest")
    document = docx.Document()
    document.add_heading("Wedge product", level=1)
    document.add_paragraph(
        "The wedge product is the antisymmetric part of the tensor product. It is bilinear "
        "and alternating, so the wedge of a covector with itself vanishes."
    )
    document.add_heading("Exterior derivative", level=2)
    document.add_paragraph(
        "The exterior derivative raises the degree of a form by one and satisfies d squared "
        "equals zero on every smooth differential form."
    )
    document.save(str(path))
    return path


def make_pptx(path: Path) -> Path:
    pptx = pytest.importorskip("pptx", reason="python-pptx is required for PPTX ingest")
    presentation = pptx.Presentation()
    layout = presentation.slide_layouts[1]  # Title and Content
    slides = [
        ("Vector spaces", "Axioms of a vector space. Bases and dimension."),
        ("Dual spaces", "The dual space is the space of linear functionals."),
        (
            "Stokes theorem",
            "The integral of an exterior derivative over a manifold equals the integral of "
            "the form over the boundary.",
        ),
    ]
    for title, body in slides:
        slide = presentation.slides.add_slide(layout)
        slide.shapes.title.text = title
        slide.placeholders[1].text = body
    presentation.save(str(path))
    return path


def make_md(path: Path) -> Path:
    path.write_text(
        "# Differential forms\n\n"
        "A differential form is a smooth section of the exterior algebra bundle.\n\n"
        "## Pullback\n\n"
        "The pullback of a form along a smooth map commutes with the exterior derivative.\n",
        encoding="utf-8",
    )
    return path


# --- tests ---------------------------------------------------------------


def test_pdf_ingest_gives_page_locators_and_flags_short_pages(corpus, source_dir):
    result = ingest_mod.ingest_file(
        corpus, GOAL, make_pdf(source_dir / "notes.pdf"), role=roles.ALIGNMENT
    )

    assert result["status"] == "ingested"
    assert result["meta"]["pages"] == 3
    # Page 3 holds four characters, well under the 20-char OCR threshold.
    assert result["meta"]["needs_ocr"] is True
    assert result["meta"]["needs_ocr_pages"] == [3]

    # The thin page still contributes its text — nothing is dropped, it is only marked.
    chunks = list_chunks(corpus, GOAL)
    pages = sorted({chunk["locator"]["page"] for chunk in chunks})
    assert pages == [1, 2, 3]
    covector_chunk = next(chunk for chunk in chunks if "dual space" in chunk["text"])
    assert covector_chunk["locator"] == {"page": 2}


def test_pdf_structure_falls_back_to_the_page_sequence(corpus, source_dir):
    ingest_mod.ingest_file(
        corpus, GOAL, make_pdf(source_dir / "notes.pdf"), role=roles.ALIGNMENT
    )
    outline = get_structure(corpus, GOAL)
    assert [entry["title"] for entry in outline] == ["Page 1", "Page 2", "Page 3"]
    assert outline[0]["locator"] == {"page": 1}


def test_a_blank_page_produces_no_chunk_but_is_still_marked_for_ocr(corpus, source_dir):
    make_pdf(source_dir / "scan.pdf", ["Readable first page about vector spaces.", ""])
    result = ingest_mod.ingest_file(corpus, GOAL, source_dir / "scan.pdf", role=roles.ALIGNMENT)

    assert result["meta"]["needs_ocr_pages"] == [2]
    assert {chunk["locator"]["page"] for chunk in list_chunks(corpus, GOAL)} == {1}


def test_docx_headings_become_structure(corpus, source_dir):
    result = ingest_mod.ingest_file(
        corpus, GOAL, make_docx(source_dir / "chapter.docx"), role=roles.AUTHORITY
    )
    assert result["kind"] == "docx"

    outline = get_structure(corpus, GOAL)
    assert [(entry["level"], entry["title"]) for entry in outline] == [
        (1, "Wedge product"),
        (2, "Exterior derivative"),
    ]
    chunks = list_chunks(corpus, GOAL)
    headings = {chunk["locator"]["heading"] for chunk in chunks}
    assert headings == {"Wedge product", "Exterior derivative"}


def test_pptx_slides_become_locators_and_titles(corpus, source_dir):
    result = ingest_mod.ingest_file(
        corpus, GOAL, make_pptx(source_dir / "deck.pptx"), role=roles.ALIGNMENT
    )
    assert result["meta"]["slides"] == 3

    outline = get_structure(corpus, GOAL)
    assert [entry["title"] for entry in outline] == [
        "Vector spaces",
        "Dual spaces",
        "Stokes theorem",
    ]
    assert [entry["locator"] for entry in outline] == [
        {"slide": 1},
        {"slide": 2},
        {"slide": 3},
    ]
    chunks = list_chunks(corpus, GOAL)
    stokes = next(chunk for chunk in chunks if "boundary" in chunk["text"])
    assert stokes["locator"] == {"slide": 3}


def test_markdown_outline_and_heading_locators(corpus, source_dir):
    ingest_mod.ingest_file(corpus, GOAL, make_md(source_dir / "notes.md"), role=roles.LEARNER)

    outline = get_structure(corpus, GOAL)
    assert [(entry["level"], entry["title"]) for entry in outline] == [
        (1, "Differential forms"),
        (2, "Pullback"),
    ]
    chunks = list_chunks(corpus, GOAL)
    assert {chunk["locator"]["heading"] for chunk in chunks} == {
        "Differential forms",
        "Pullback",
    }


def test_same_bytes_ingested_twice_is_one_source(corpus, source_dir):
    path = make_md(source_dir / "notes.md")
    first = ingest_mod.ingest_file(corpus, GOAL, path, role=roles.LEARNER)
    second = ingest_mod.ingest_file(corpus, GOAL, path, role=roles.LEARNER)

    assert first["status"] == "ingested"
    assert second["status"] == "duplicate"
    assert second["source_id"] == first["source_id"]
    assert len(list_sources(corpus, GOAL)) == 1


def test_chunking_never_crosses_a_locator_boundary(corpus, source_dir):
    """A long section windows inside its own locator; it never spills into the next one."""

    long_section = " ".join(f"token{index}" for index in range(1200))
    path = source_dir / "long.md"
    path.write_text(
        "\n".join(
            [
                "# Long section",
                "",
                long_section,
                "",
                "## Short section",
                "",
                "A single closing sentence.",
                "",
            ]
        ),
        encoding="utf-8",
    )
    ingest_mod.ingest_file(corpus, GOAL, path, role=roles.ALIGNMENT)

    chunks = list_chunks(corpus, GOAL)
    long_chunks = [chunk for chunk in chunks if chunk["locator"] == {"heading": "Long section"}]
    assert len(long_chunks) > 1, "a 1200-word section should window into several chunks"
    assert all(chunk["tokens_est"] <= 450 for chunk in long_chunks)

    # 60 tokens of overlap: consecutive windows share their boundary words.
    overlap = set(long_chunks[0]["text"].split()) & set(long_chunks[1]["text"].split())
    assert len(overlap) >= 30

    # Nothing from the long section carries the other section's locator, and vice versa.
    short_chunks = [chunk for chunk in chunks if chunk["locator"] == {"heading": "Short section"}]
    assert len(short_chunks) == 1
    assert "token0" not in short_chunks[0]["text"]
    assert "closing sentence" not in " ".join(chunk["text"] for chunk in long_chunks)


def test_roles_are_validated(corpus, source_dir):
    with pytest.raises(ValueError):
        ingest_mod.ingest_file(corpus, GOAL, make_md(source_dir / "n.md"), role="scripture")


def test_unsupported_extension_is_a_clear_error(corpus, source_dir):
    path = source_dir / "slides.key"
    path.write_bytes(b"not a supported format")
    with pytest.raises(CorpusError, match="unsupported file type"):
        ingest_mod.ingest_file(corpus, GOAL, path, role=roles.ALIGNMENT)


def test_relative_paths_cannot_escape_the_goal_folder(corpus, source_dir):
    make_md(source_dir / "notes.md")
    assert ingest_mod.resolve_path(GOAL, "notes.md", corpus.settings).exists()
    with pytest.raises(CorpusError, match="escapes the goal source directory"):
        ingest_mod.resolve_path(GOAL, "../../etc/passwd", corpus.settings)


@pytest.mark.parametrize(
    ("call", "env"),
    [
        (lambda store: ingest_mod.transcribe_audio(Path("lecture.mp3")), "LT_TRANSCRIBE_URL"),
        (lambda store: ingest_mod.fetch_youtube("https://youtu.be/x"), "LT_YT_TRANSCRIPTS_URL"),
        (lambda store: ingest_mod.ocr_pages(Path("scan.pdf"), [1]), "LT_OCR_URL"),
    ],
)
def test_optional_adapters_say_service_not_configured(corpus, call, env):
    with pytest.raises(CorpusError) as excinfo:
        call(corpus)
    assert "service not configured" in str(excinfo.value)
    assert env in str(excinfo.value)


def test_youtube_adapter_uses_the_configured_url(corpus, monkeypatch):
    """No host is hardcoded: the adapter posts wherever LT_YT_TRANSCRIPTS_URL points."""

    seen: dict[str, object] = {}

    def fake_post(url, payload, timeout=300.0):
        seen["url"] = url
        seen["payload"] = payload
        return {
            "title": "Lecture 4 — differential forms",
            "segments": [
                {"start": 0, "text": "Today we introduce the exterior derivative."},
                {"start": 751, "text": "Stokes theorem relates a form to its boundary."},
            ],
        }

    monkeypatch.setenv("LT_YT_TRANSCRIPTS_URL", "http://transcripts.internal:8000")
    monkeypatch.setattr(ingest_mod, "_post_json", fake_post)

    result = ingest_mod.ingest_youtube(
        corpus, GOAL, "https://youtu.be/abc", role=roles.AUTHORITY
    )
    assert seen["url"] == "http://transcripts.internal:8000/transcript"
    assert result["status"] == "ingested"
    assert result["kind"] == "youtube"

    chunks = list_chunks(corpus, GOAL)
    assert {chunk["locator"]["t"] for chunk in chunks} == {"00:00:00", "00:12:31"}
