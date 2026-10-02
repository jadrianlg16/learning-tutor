"""Router smoke test over the real FastAPI app, via TestClient.

The gateway mounts this router in-process (CONTRACTS.md: gateway + llm + tutor + corpus in
one container), so the contract that matters is the HTTP shape, not a network hop.
"""

from __future__ import annotations

import pytest

fastapi = pytest.importorskip("fastapi", reason="FastAPI is required for the corpus router")
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from learning_tutor.corpus import roles  # noqa: E402
from learning_tutor.corpus.router import router  # noqa: E402
from learning_tutor.corpus.store import goal_sources_dir  # noqa: E402

GOAL = "g_linalg"

SLIDE_MD = (
    "# Determinants\n\n"
    "The determinant of a rotation matrix is 1.\n\n"
    "# Orthogonality\n\n"
    "An orthogonal matrix has orthonormal columns.\n"
)

BOOK_MD = (
    "# Determinants of orthogonal matrices\n\n"
    "The determinant of an orthogonal matrix is not always 1: a reflection has "
    "determinant -1.\n"
)


@pytest.fixture(autouse=True)
def offline_embeddings(monkeypatch):
    monkeypatch.setenv("LT_EMBED_MODEL", "hash")


@pytest.fixture
def client(settings):
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        yield client


@pytest.fixture
def source_dir(settings):
    path = goal_sources_dir(GOAL, settings)
    path.mkdir(parents=True, exist_ok=True)
    return path


@pytest.fixture
def loaded(client, source_dir):
    (source_dir / "deck.md").write_text(SLIDE_MD, encoding="utf-8")
    (source_dir / "book.md").write_text(BOOK_MD, encoding="utf-8")
    client.post(
        f"/corpus/{GOAL}/ingest",
        json={"path": "deck.md", "role": roles.ALIGNMENT, "title": "Course deck"},
    ).raise_for_status()
    client.post(
        f"/corpus/{GOAL}/ingest",
        json={"path": "book.md", "role": roles.AUTHORITY, "title": "Textbook"},
    ).raise_for_status()
    return client


def test_the_router_imports_and_reports_health_without_a_network(client):
    response = client.get("/corpus/health")
    assert response.status_code == 200

    body = response.json()
    assert body["ok"] is True
    assert body["schema_version"] == 1
    assert body["embeddings"]["needs_network"] is False
    assert body["roles"] == ["alignment", "authority", "learner"]


def test_ingest_by_path_relative_to_the_goal_folder(client, source_dir):
    (source_dir / "deck.md").write_text(SLIDE_MD, encoding="utf-8")
    response = client.post(
        f"/corpus/{GOAL}/ingest",
        json={"path": "deck.md", "role": roles.ALIGNMENT, "title": "Course deck"},
    )
    assert response.status_code == 200

    body = response.json()
    assert body["status"] == "ingested"
    assert body["chunks"] == 2
    assert body["structure"] == 2
    assert body["embedded"] == 2


def test_ingest_by_multipart_upload(client):
    response = client.post(
        f"/corpus/{GOAL}/ingest",
        files={"file": ("uploaded.md", SLIDE_MD.encode("utf-8"), "text/markdown")},
        data={"role": roles.ALIGNMENT, "title": "Uploaded deck"},
    )
    assert response.status_code == 200

    body = response.json()
    assert body["status"] == "ingested"
    assert body["title"] == "Uploaded deck"
    assert body["kind"] == "md"

    sources = client.get(f"/corpus/{GOAL}/sources").json()
    assert sources["count"] == 1
    assert sources["sources"][0]["role"] == roles.ALIGNMENT


def test_ingest_rejects_a_body_with_nothing_to_ingest(client):
    response = client.post(f"/corpus/{GOAL}/ingest", json={"role": roles.ALIGNMENT})
    assert response.status_code == 400
    assert "path, text or url" in response.json()["detail"]


def test_sources_endpoint_lists_roles_and_their_rules(loaded):
    body = loaded.get(f"/corpus/{GOAL}/sources").json()
    assert body["count"] == 2
    assert {source["role"] for source in body["sources"]} == {"alignment", "authority"}

    rules = {entry["role"]: entry for entry in body["roles"]}
    assert rules["alignment"]["proves"] == "alignment"
    assert rules["authority"]["proves"] == "correctness"


def test_sources_endpoint_filters_by_role(loaded):
    body = loaded.get(f"/corpus/{GOAL}/sources", params={"role": "authority"}).json()
    assert body["count"] == 1
    assert body["sources"][0]["title"] == "Textbook"

    bad = loaded.get(f"/corpus/{GOAL}/sources", params={"role": "scripture"})
    assert bad.status_code == 400


def test_structure_endpoint_returns_the_document_outline(loaded):
    body = loaded.get(f"/corpus/{GOAL}/structure").json()
    titles = [entry["title"] for entry in body["structure"]]
    assert "Determinants" in titles
    assert "Determinants of orthogonal matrices" in titles
    assert body["structure"][0]["locator"] == {"heading": "Determinants"}


def test_search_endpoint_returns_scored_hits_with_locators(loaded):
    body = loaded.get(
        f"/corpus/{GOAL}/search", params={"q": "determinant rotation matrix", "k": 3}
    ).json()
    assert body["count"] >= 1

    top = body["results"][0]
    assert top["locator"] == {"heading": "Determinants"}
    assert top["role"] == "alignment"
    assert top["proves"] == "alignment"
    assert top["score"] > 0
    assert body["diagnostics"]["lexical_method"] == "sqlite_fts"


def test_search_endpoint_honours_the_role_filter(loaded):
    body = loaded.get(
        f"/corpus/{GOAL}/search", params={"q": "determinant", "role": "authority"}
    ).json()
    assert {hit["role"] for hit in body["results"]} == {"authority"}


def test_cite_endpoint_cites_then_abstains(loaded):
    cited = loaded.post(
        f"/corpus/{GOAL}/cite",
        json={"claim": "the determinant of a rotation matrix is 1", "k": 2},
    ).json()
    assert cited["status"] == "cited"
    assert cited["citations"][0]["proves"] == "alignment"
    assert cited["citations"][0]["locator"] == {"heading": "Determinants"}

    abstained = loaded.post(
        f"/corpus/{GOAL}/cite",
        json={"claim": "the Hodge star depends on a choice of orientation"},
    ).json()
    assert abstained["status"] == "abstain"
    assert "citations" not in abstained


def test_conflicts_endpoint_returns_two_labelled_readings(loaded):
    body = loaded.get(
        f"/corpus/{GOAL}/conflicts", params={"topic": "determinant of an orthogonal matrix"}
    ).json()
    assert body["status"] == "conflict"
    assert [reading["mode"] for reading in body["readings"]] == ["exam-mode", "truth-mode"]
    assert body["readings"][1]["proves"] == "correctness"


def test_sources_md_round_trips_over_http(client):
    empty = client.get(f"/corpus/{GOAL}/sources-md").json()
    assert empty["exists"] is False
    assert empty["spec"]["depth"] == "explain"

    written = client.put(
        f"/corpus/{GOAL}/sources-md",
        json={"depth": "apply", "trusted": ["https://ncatlab.org"], "notation": "slides"},
    ).json()
    assert written["spec"]["depth"] == "apply"

    reread = client.get(f"/corpus/{GOAL}/sources-md").json()
    assert reread["exists"] is True
    assert reread["spec"]["trusted"] == ["https://ncatlab.org"]
    assert "## Trusted sources" in reread["markdown"]


def test_sources_md_accepts_raw_markdown(client):
    markdown = "# Sources\n\n## Preferences\n\n- depth: analyze\n\n## Banned sources\n\n- seo spam\n"
    body = client.put(f"/corpus/{GOAL}/sources-md", json={"markdown": markdown}).json()
    assert body["spec"]["depth"] == "analyze"
    assert body["spec"]["banned"] == ["seo spam"]


def test_research_propose_then_approve(client):
    client.put(
        f"/corpus/{GOAL}/sources-md",
        json={"preferred_textbooks": ["Strang"], "trusted": ["https://ncatlab.org"]},
    ).raise_for_status()

    proposed = client.post(
        f"/corpus/{GOAL}/research/propose", json={"topic": "orthogonal matrices"}
    ).json()
    assert proposed["status"] == "proposed"
    assert len(proposed["sources"]) == 2

    approved = client.post(
        f"/corpus/{GOAL}/research/approve",
        json={"list_id": proposed["list_id"], "accept": ["Strang"]},
    ).json()
    assert approved["status"] == "approved"
    assert [entry["title"] for entry in approved["approved"]] == ["Strang"]

    index = client.get(f"/corpus/{GOAL}/research").json()
    assert index["lists"][0]["status"] == "approved"


def test_research_propose_needs_a_topic(client):
    response = client.post(f"/corpus/{GOAL}/research/propose", json={})
    assert response.status_code == 400
    assert "topic" in response.json()["detail"]


def test_context_endpoint_is_fenced_quoted_and_visibly_truncated(loaded):
    whole = loaded.get(f"/corpus/{GOAL}/context", params={"max_tokens": 50_000}).json()
    assert whole["truncated"] is False
    assert whole["chunks_rendered"] == whole["chunks_total"] == 3
    assert whole["text"].startswith("The blocks below are QUOTED SOURCE MATERIAL")
    assert "<<<SOURCE id=" in whole["text"]
    assert "<<<END SOURCE>>>" in whole["text"]
    assert whole["structure"]

    clipped = loaded.get(f"/corpus/{GOAL}/context", params={"max_tokens": 120}).json()
    assert clipped["truncated"] is True
    assert clipped["chunks_omitted"] > 0
    assert "<<<CONTEXT TRUNCATED" in clipped["text"]


def test_context_endpoint_can_be_scoped_to_one_role(loaded):
    body = loaded.get(f"/corpus/{GOAL}/context", params={"role": "authority"}).json()
    assert body["chunks_total"] == 1
    assert "role=authority" in body["text"]
    assert "role=alignment" not in body["text"]


def test_deleting_a_source_removes_its_chunks_from_search(loaded):
    sources = loaded.get(f"/corpus/{GOAL}/sources").json()["sources"]
    authority = next(source for source in sources if source["role"] == "authority")

    deleted = loaded.delete(f"/corpus/{GOAL}/sources/{authority['source_id']}")
    assert deleted.status_code == 200

    after = loaded.get(
        f"/corpus/{GOAL}/search", params={"q": "reflection determinant", "role": "authority"}
    ).json()
    assert after["count"] == 0
    assert loaded.delete(f"/corpus/{GOAL}/sources/s_missing").status_code == 404


def test_importing_every_corpus_module_opens_no_socket():
    """Evidence for "must import without network": an audit hook trips on any connect."""

    import subprocess
    import sys

    script = "\n".join(
        [
            "import sys",
            "def hook(event, args):",
            "    if event in ('socket.connect', 'socket.getaddrinfo', 'urllib.Request'):",
            "        raise AssertionError('import touched the network: ' + event)",
            "sys.addaudithook(hook)",
            "import learning_tutor.corpus.router",
            "import learning_tutor.corpus.ingest",
            "import learning_tutor.corpus.search",
            "import learning_tutor.corpus.cite",
            "import learning_tutor.corpus.embed",
            "import learning_tutor.corpus.research",
            "import learning_tutor.corpus.sources_md",
            "print('clean')",
        ]
    )
    completed = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert "clean" in completed.stdout
