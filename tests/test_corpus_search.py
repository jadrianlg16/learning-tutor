"""Hybrid search, cite-or-abstain, and the alignment/authority conflict view.

The corpus here is deliberately small and contradictory: the deck (alignment) states a flat
rule, the textbook (authority) states the exception. That is the exam-mode vs truth-mode case
IDEA.md asks for, and it is what ``conflicts`` has to surface without picking a side.
"""

from __future__ import annotations

import pytest

from learning_tutor.corpus import cite as cite_mod
from learning_tutor.corpus import embed as embed_mod
from learning_tutor.corpus import ingest as ingest_mod
from learning_tutor.corpus import roles
from learning_tutor.corpus.search import search, support
from learning_tutor.corpus.store import goal_sources_dir, open_store

GOAL = "g_linalg"

SLIDES = [
    ("Orthogonal matrices", "An orthogonal matrix has orthonormal columns. Its inverse is its transpose."),
    ("Determinants", "The determinant of a rotation matrix is 1."),
    ("Eigenvalues", "A symmetric matrix has real eigenvalues and an orthonormal eigenbasis."),
]

TEXTBOOK = (
    "# Orthogonal group\n\n"
    "The orthogonal group splits into two components.\n\n"
    "# Determinants of orthogonal matrices\n\n"
    "The determinant of an orthogonal matrix is not always 1: a reflection has "
    "determinant -1, and only the rotation component has determinant 1.\n"
)

NOTES = (
    "# My notes\n\n"
    "I keep confusing the transpose with the inverse for orthogonal matrices.\n"
)


@pytest.fixture(autouse=True)
def offline_embeddings(monkeypatch):
    monkeypatch.setenv("LT_EMBED_MODEL", "hash")


@pytest.fixture
def corpus(settings):
    with open_store(settings) as store:
        yield store


@pytest.fixture
def loaded(corpus, settings):
    """A deck (alignment), a textbook (authority) and the learner's own notes."""

    pptx = pytest.importorskip("pptx", reason="python-pptx is required for PPTX ingest")
    source_dir = goal_sources_dir(GOAL, settings)
    source_dir.mkdir(parents=True, exist_ok=True)

    presentation = pptx.Presentation()
    layout = presentation.slide_layouts[1]
    for title, body in SLIDES:
        slide = presentation.slides.add_slide(layout)
        slide.shapes.title.text = title
        slide.placeholders[1].text = body
    deck = source_dir / "deck.pptx"
    presentation.save(str(deck))

    ingest_mod.ingest_file(corpus, GOAL, deck, role=roles.ALIGNMENT, title="Course deck")
    ingest_mod.ingest_text(
        corpus, GOAL, TEXTBOOK, role=roles.AUTHORITY, title="Linear algebra textbook"
    )
    ingest_mod.ingest_text(corpus, GOAL, NOTES, role=roles.LEARNER, title="My notes")
    return corpus


# --- embeddings ----------------------------------------------------------


def test_hash_backend_is_deterministic_and_offline():
    first = embed_mod.hash_embed("the determinant of a rotation matrix")
    second = embed_mod.hash_embed("the determinant of a rotation matrix")
    other = embed_mod.hash_embed("eigenvalues of a symmetric matrix")

    assert first == second
    assert len(first) == 256
    assert embed_mod.cosine(first, second) == pytest.approx(1.0)
    assert embed_mod.cosine(first, other) < 0.9


def test_float32_blob_round_trips():
    vector = embed_mod.hash_embed("wedge product", dim=64)
    restored = embed_mod.from_blob(embed_mod.to_blob(vector), 64)
    assert len(restored) == 64
    assert embed_mod.cosine(vector, restored) == pytest.approx(1.0, abs=1e-6)


def test_backend_info_reports_no_network_need(monkeypatch):
    monkeypatch.setenv("LT_EMBED_MODEL", "hash")
    assert embed_mod.backend_info()["needs_network"] is False
    monkeypatch.setenv("LT_EMBED_MODEL", "nomic-embed-text")
    info = embed_mod.backend_info()
    assert info["needs_network"] is True and info["backend"] == "ollama"


# --- search --------------------------------------------------------------


def test_search_finds_the_right_slide(loaded):
    results = search(loaded, GOAL, "determinant of a rotation matrix", k=3)
    assert results
    top = results[0]
    assert top["locator"] == {"slide": 2}
    assert top["role"] == roles.ALIGNMENT
    assert top["score"] > 0


def test_search_fuses_both_rankers(loaded):
    results, diagnostics = search(
        loaded, GOAL, "orthonormal columns transpose", k=5, with_diagnostics=True
    )
    assert diagnostics["lexical_method"] == "sqlite_fts"
    assert diagnostics["semantic"]["used"] is True
    assert diagnostics["method"] == "hybrid_rrf"
    assert diagnostics["rrf_k"] == 60
    top = results[0]
    assert top["lexical_rank"] is not None and top["semantic_rank"] is not None
    assert top["score"] == pytest.approx(1 / 61 + 1 / 61)


def test_role_filter_restricts_results(loaded):
    authority = search(loaded, GOAL, "determinant orthogonal matrix", k=5, role=roles.AUTHORITY)
    assert authority
    assert {hit["role"] for hit in authority} == {roles.AUTHORITY}
    assert {hit["proves"] for hit in authority} == {"correctness"}

    learner = search(loaded, GOAL, "transpose inverse", k=5, role=roles.LEARNER)
    assert {hit["proves"] for hit in learner} == {"learner_evidence"}


def test_source_filter_restricts_results(loaded):
    everything = search(loaded, GOAL, "matrix", k=20)
    source_ids = {hit["source_id"] for hit in everything}
    assert len(source_ids) > 1

    one = source_ids.pop()
    scoped = search(loaded, GOAL, "matrix", k=20, source_id=one)
    assert {hit["source_id"] for hit in scoped} == {one}


def test_support_measures_claim_coverage():
    assert support("rotation matrix determinant", "the determinant of a rotation matrix is 1") == 1.0
    assert support("rotation matrix determinant", "eigenvalues of a symmetric matrix") < 0.5


# --- cite or abstain -----------------------------------------------------


def test_cite_hits_the_right_slide_and_stamps_alignment(loaded):
    result = cite_mod.cite_or_abstain(
        loaded, "the determinant of a rotation matrix is 1", GOAL, k=3
    )
    assert result["status"] == "cited"

    citation = result["citations"][0]
    assert citation["locator"] == {"slide": 2}
    assert citation["title"] == "Course deck"
    assert citation["role"] == roles.ALIGNMENT
    # Citing a slide proves alignment, not truth.
    assert citation["proves"] == "alignment"
    assert len(citation["quote"]) <= 200
    assert "determinant of a rotation matrix is 1" in citation["quote"]


def test_cite_against_the_textbook_proves_correctness(loaded):
    result = cite_mod.cite_or_abstain(
        loaded,
        "an orthogonal matrix can have determinant -1",
        GOAL,
        k=3,
        role=roles.AUTHORITY,
    )
    assert result["status"] == "cited"
    assert result["proves"] == ["correctness"]
    assert result["citations"][0]["locator"] == {"heading": "Determinants of orthogonal matrices"}


def test_cite_abstains_on_a_claim_the_corpus_does_not_cover(loaded):
    result = cite_mod.cite_or_abstain(
        loaded, "the Hodge star operator depends on a choice of orientation", GOAL
    )
    assert result["status"] == "abstain"
    assert "supports only" in result["reason"] or "no corpus material" in result["reason"]
    assert "citations" not in result


def test_cite_abstains_on_an_empty_or_contentless_claim(loaded):
    assert cite_mod.cite_or_abstain(loaded, "", GOAL)["status"] == "abstain"
    assert cite_mod.cite_or_abstain(loaded, "the and of a", GOAL)["status"] == "abstain"


def test_min_score_is_the_knob_between_citing_and_abstaining(loaded):
    claim = "a symmetric matrix has real eigenvalues and unicorns"
    assert cite_mod.cite_or_abstain(loaded, claim, GOAL, min_score=0.95)["status"] == "abstain"
    assert cite_mod.cite_or_abstain(loaded, claim, GOAL, min_score=0.5)["status"] == "cited"


def test_quotes_are_capped_at_200_characters(loaded):
    long_text = "# Long\n\n" + " ".join(f"determinant{index}" for index in range(300))
    ingest_mod.ingest_text(loaded, GOAL, long_text, role=roles.AUTHORITY, title="Long doc")
    result = cite_mod.cite_or_abstain(loaded, "determinant0 determinant1", GOAL, min_score=0.5)
    assert result["status"] == "cited"
    assert all(len(citation["quote"]) <= 200 for citation in result["citations"])


# --- conflicts -----------------------------------------------------------


def test_conflicts_returns_two_labelled_readings(loaded):
    result = cite_mod.conflicts(loaded, GOAL, "determinant of an orthogonal matrix")

    assert result["status"] == "conflict"
    assert set(result["basis"]) & {"negation", "numeric"}

    exam, truth = result["readings"]
    assert exam["mode"] == "exam-mode"
    assert exam["role"] == roles.ALIGNMENT
    assert exam["proves"] == "alignment"
    assert exam["locator"] == {"slide": 2}

    assert truth["mode"] == "truth-mode"
    assert truth["role"] == roles.AUTHORITY
    assert truth["proves"] == "correctness"
    assert "-1" in truth["quote"]

    # Flagged, never resolved.
    assert "Not resolved" in result["resolution"]
    assert "HYPOTHESIS" in result["heuristic"]


def test_conflicts_says_agree_when_the_roles_do_not_disagree(loaded):
    ingest_mod.ingest_text(
        loaded,
        GOAL,
        "# Symmetric matrices\n\nA symmetric matrix has real eigenvalues and an "
        "orthonormal eigenbasis.\n",
        role=roles.AUTHORITY,
        title="Textbook chapter 7",
    )
    result = cite_mod.conflicts(loaded, GOAL, "symmetric matrix real eigenvalues")
    assert result["status"] == "agree"
    assert [reading["mode"] for reading in result["readings"]] == ["exam-mode", "truth-mode"]


def test_conflicts_needs_both_roles(corpus):
    ingest_mod.ingest_text(
        corpus, GOAL, "# Only slides\n\nThe trace is the sum of the diagonal.\n",
        role=roles.ALIGNMENT, title="Deck",
    )
    result = cite_mod.conflicts(corpus, GOAL, "trace diagonal")
    assert result["status"] == "insufficient"
    assert "authority" in result["reason"]


def test_the_conflict_heuristic_in_isolation():
    negation = cite_mod.disagreement(
        "The determinant of a rotation matrix is 1.",
        "The determinant of an orthogonal matrix is not always 1.",
        "determinant matrix",
    )
    assert negation["conflict"] is True
    assert "negation" in negation["basis"]

    numeric = cite_mod.disagreement(
        "The bound on the error term is 5 percent.",
        "The bound on the error term is 12 percent.",
        "bound error term",
    )
    assert numeric["conflict"] is True
    assert "numeric" in numeric["basis"]

    unrelated = cite_mod.disagreement(
        "The determinant of a rotation matrix is 1.",
        "Eigenvectors of distinct eigenvalues are independent.",
        "linear algebra",
    )
    assert unrelated["conflict"] is False


def test_ingest_and_search_survive_an_unreachable_embedding_backend(corpus, monkeypatch):
    """A dead Ollama degrades search to FTS. It never fails the ingest."""

    def refused(texts, model=None):
        raise embed_mod.EmbeddingError("embedding request failed: connection refused")

    monkeypatch.setattr(embed_mod, "embed_texts", refused)
    result = ingest_mod.ingest_text(
        corpus,
        GOAL,
        "# Determinants\n\nThe determinant of a rotation matrix is 1.\n",
        role=roles.ALIGNMENT,
        title="Course deck",
    )
    assert result["status"] == "ingested"
    assert result["embedded"] == 0
    assert "connection refused" in result["embed_error"]

    results, diagnostics = search(
        corpus, GOAL, "determinant rotation matrix", with_diagnostics=True
    )
    assert results, "lexical search must still work with no embeddings"
    assert diagnostics["lexical_method"] == "sqlite_fts"
    assert diagnostics["semantic"]["used"] is False
    assert diagnostics["method"] == "single_ranker"
