"""Per-goal ``sources.md`` and the research pass.

The research pass does no web research here on purpose: IDEA.md puts that in the harness,
because the research pass is exactly where invented sources enter. This module owns the
approval trail — proposed, approved, ingested — and nothing else.
"""

from __future__ import annotations

import pytest

from learning_tutor.corpus import research, roles, sources_md
from learning_tutor.corpus.store import CorpusError, open_store

GOAL = "g_linalg"

HAND_WRITTEN = """# Sources — g_linalg

## Preferences

- notation: use the slides' notation for inner products
- depth: apply
- language: en
- exam_format: closed book, 3 hours, written proofs
- source_priority: alignment

## Trusted sources

- https://ncatlab.org
- Axler, Linear Algebra Done Right

## Banned sources

- random blog posts
- content farms

## Preferred textbooks

- Strang, Introduction to Linear Algebra

## Notes

The professor marks notation strictly.
"""


@pytest.fixture(autouse=True)
def offline_embeddings(monkeypatch):
    monkeypatch.setenv("LT_EMBED_MODEL", "hash")


@pytest.fixture
def corpus(settings):
    with open_store(settings) as store:
        yield store


# --- sources.md ----------------------------------------------------------


def test_parse_reads_preferences_and_lists():
    spec = sources_md.parse(HAND_WRITTEN, goal_id=GOAL)

    assert spec.notation == "use the slides' notation for inner products"
    assert spec.depth == "apply"
    assert spec.language == "en"
    assert spec.exam_format == "closed book, 3 hours, written proofs"
    assert spec.source_priority == "alignment"
    assert spec.trusted == ["https://ncatlab.org", "Axler, Linear Algebra Done Right"]
    assert spec.banned == ["random blog posts", "content farms"]
    assert spec.preferred_textbooks == ["Strang, Introduction to Linear Algebra"]
    assert "notation strictly" in spec.notes


def test_render_round_trips_through_parse():
    spec = sources_md.parse(HAND_WRITTEN, goal_id=GOAL)
    again = sources_md.parse(sources_md.render(spec), goal_id=GOAL)

    assert again.trusted == spec.trusted
    assert again.banned == spec.banned
    assert again.preferred_textbooks == spec.preferred_textbooks
    assert again.notation == spec.notation
    assert again.exam_format == spec.exam_format


def test_missing_file_gives_documented_defaults(settings):
    spec = sources_md.load(GOAL, settings)
    assert spec.goal_id == GOAL
    assert spec.depth == "explain"
    assert spec.source_priority == "alignment"
    assert spec.trusted == []


def test_save_and_load_use_the_goal_source_folder(settings):
    spec = sources_md.parse(HAND_WRITTEN, goal_id=GOAL)
    path = sources_md.save(GOAL, spec, settings)

    assert path.name == "sources.md"
    assert path.parent.name == GOAL
    assert path.parent.parent.name == "sources"
    assert sources_md.load(GOAL, settings).trusted == spec.trusted


def test_update_changes_only_what_is_named(settings):
    sources_md.save_text(GOAL, HAND_WRITTEN, settings)
    spec = sources_md.update(GOAL, {"depth": "analyze", "banned": ["seo spam"]}, settings)

    assert spec.depth == "analyze"
    assert spec.banned == ["seo spam"]
    assert spec.trusted == ["https://ncatlab.org", "Axler, Linear Algebra Done Right"]


def test_is_banned_matches_either_direction(settings):
    spec = sources_md.parse(HAND_WRITTEN, goal_id=GOAL)
    assert spec.is_banned("some random blog posts about matrices")
    assert not spec.is_banned("https://ncatlab.org/nlab/show/determinant")


# --- research ------------------------------------------------------------


def test_propose_builds_a_list_from_sources_md(corpus, settings):
    sources_md.save_text(GOAL, HAND_WRITTEN, settings)
    proposal = research.propose(corpus, GOAL, "orthogonal matrices")

    assert proposal["status"] == "proposed"
    titles = [entry["title"] for entry in proposal["sources"]]
    assert "Strang, Introduction to Linear Algebra" in titles
    assert "https://ncatlab.org" in titles
    assert {entry["origin"] for entry in proposal["sources"]} == {"sources_md"}
    assert all(entry["proves"] == "correctness" for entry in proposal["sources"])
    assert all(entry["source_id"] is None for entry in proposal["sources"])

    brief = proposal["search_briefs"][0]
    assert brief["notation"] == "use the slides' notation for inner products"
    assert brief["avoid"] == ["random blog posts", "content farms"]
    assert "apply-level" in brief["instruction"]
    assert "never this module" in brief["run_by"]


def test_harness_supplied_candidates_are_marked_and_banned_ones_rejected(corpus, settings):
    sources_md.save_text(GOAL, HAND_WRITTEN, settings)
    proposal = research.propose(
        corpus,
        GOAL,
        "orthogonal matrices",
        extra=[
            {"title": "MIT 18.06 lecture notes", "url": "https://ocw.mit.edu/x", "role": "authority"},
            {"title": "random blog posts on matrices", "url": "https://example.invalid/blog"},
        ],
    )
    by_title = {entry["title"]: entry for entry in proposal["sources"]}

    assert by_title["MIT 18.06 lecture notes"]["origin"] == "harness_research"
    assert by_title["MIT 18.06 lecture notes"]["status"] == "proposed"
    assert by_title["random blog posts on matrices"]["status"] == "banned"


def test_nothing_is_ingested_until_a_list_is_approved(corpus, settings):
    sources_md.save_text(GOAL, HAND_WRITTEN, settings)
    proposal = research.propose(corpus, GOAL, "orthogonal matrices")
    assert "Nothing here is ingested" in proposal["note"]

    stored = research.get_list(corpus, proposal["list_id"])
    assert stored["status"] == "proposed"
    assert stored["decided_at"] is None


def test_approve_keeps_only_the_accepted_entries(corpus, settings):
    sources_md.save_text(GOAL, HAND_WRITTEN, settings)
    proposal = research.propose(corpus, GOAL, "orthogonal matrices")

    decision = research.approve(
        corpus, proposal["list_id"], accept=["https://ncatlab.org"]
    )
    assert decision["status"] == "approved"
    assert [entry["title"] for entry in decision["approved"]] == ["https://ncatlab.org"]
    assert len(decision["rejected"]) == 2

    stored = research.get_list(corpus, proposal["list_id"])
    assert stored["status"] == "approved"
    assert stored["decided_at"]


def test_approve_with_no_accept_list_approves_everything_not_banned(corpus, settings):
    sources_md.save_text(GOAL, HAND_WRITTEN, settings)
    proposal = research.propose(
        corpus, GOAL, "orthogonal matrices",
        extra=[{"title": "content farms roundup", "url": "https://example.invalid/x"}],
    )
    decision = research.approve(corpus, proposal["list_id"])
    assert all(entry["status"] != "banned" for entry in decision["approved"])
    assert any(entry["status"] == "banned" for entry in decision["rejected"])


def test_mark_ingested_links_an_entry_to_its_source(corpus, settings):
    sources_md.save_text(GOAL, HAND_WRITTEN, settings)
    proposal = research.propose(corpus, GOAL, "orthogonal matrices")
    research.approve(corpus, proposal["list_id"])
    research.mark_ingested(
        corpus, proposal["list_id"], title_or_url="https://ncatlab.org", source_id="s_deadbeef"
    )

    stored = research.get_list(corpus, proposal["list_id"])
    entry = next(item for item in stored["sources"] if item["title"] == "https://ncatlab.org")
    assert entry["status"] == "ingested"
    assert entry["source_id"] == "s_deadbeef"


def test_unknown_list_and_empty_topic_are_clear_errors(corpus):
    with pytest.raises(CorpusError, match="unknown research list"):
        research.get_list(corpus, "rl_nope")
    with pytest.raises(CorpusError, match="needs a topic"):
        research.propose(corpus, GOAL, "  ")


def test_roles_describe_the_epistemic_rules():
    described = {entry["role"]: entry for entry in roles.describe()}
    assert described[roles.ALIGNMENT]["proves"] == "alignment"
    assert described[roles.AUTHORITY]["proves"] == "correctness"
    assert described[roles.LEARNER]["proves"] == "learner_evidence"
    assert roles.mode(roles.ALIGNMENT) == "exam-mode"
    assert roles.mode(roles.AUTHORITY) == "truth-mode"
    assert roles.can_prove_correctness(roles.ALIGNMENT) is False
