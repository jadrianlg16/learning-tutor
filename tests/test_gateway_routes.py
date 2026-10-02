"""The gateway, end to end, with a fake model and a real learner-svc.

learner-svc is mounted in-process (its own ``create_app``, reached through a sync httpx
transport backed by ``TestClient``), so every event the flow writes lands in a real SQLite
event table and every state the flow reads was recomputed by the real evidence rules. Only
the model is fake — and the point of the fake is that nothing it returns is allowed to
decide anything.
"""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from learning_tutor.gateway.app import create_app
from learning_tutor.gateway.routes import ops as ops_mod
from learning_tutor.learner_svc.app import create_app as create_learner_app
from learning_tutor.tutor import generate as generate_mod
from learning_tutor.tutor.generate import (
    Feedback,
    HintText,
    InterruptAnswer,
    MermaidFix,
    MisconceptionProbe,
    MisconceptionStepOutcome,
    PlanGraph,
    ProbeItem,
    SolverVerdict,
    TeachBackGrade,
    TeachStep,
)

GOAL = "differential-forms"

MARKDOWN_SOURCE = """# Differential forms — course notes

## Vector spaces
A vector space is a set with addition and scalar multiplication.

## Covectors
A covector is a linear map from a vector space to the reals.

## Wedge product
The wedge product is antisymmetric: u wedge v equals minus v wedge u.
"""


# --------------------------------------------------------------------- transports


class SyncASGITransport(httpx.BaseTransport):
    """A synchronous httpx transport over a Starlette app.

    ``httpx.ASGITransport`` is async-only, and the gateway's clients are sync because
    ``llm.generate_structured`` cannot be called from inside a running event loop (see
    docs/modules/llm.md). ``TestClient`` already owns a sync transport over an ASGI app;
    this hands the gateway that transport.
    """

    def __init__(self, app) -> None:
        self._client = TestClient(app)

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        headers = {
            key: value
            for key, value in request.headers.items()
            if key.lower() not in ("host", "content-length", "transfer-encoding")
        }
        response = self._client.request(
            request.method, str(request.url), content=request.read(), headers=headers
        )
        return httpx.Response(
            response.status_code,
            headers=[(k, v) for k, v in response.headers.items() if k.lower() != "content-encoding"],
            content=response.content,
        )


def render_transport(*, ok: bool = True) -> httpx.MockTransport:
    """A stand-in render-svc: /check, /render, /latex/check, /healthz."""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/check":
            body = json.loads(request.content)
            good = ok or "fixed" in body.get("mermaid", "")
            return httpx.Response(
                200,
                json={"ok": good, "errors": [] if good else [{"line": 1, "message": "boom"}]},
            )
        if path == "/render":
            return httpx.Response(200, json={"svg": "<svg>ok</svg>", "warnings": []})
        if path == "/latex/check":
            return httpx.Response(200, json={"ok": True, "errors": []})
        return httpx.Response(200, json={"ok": True, "version": "test"})

    return httpx.MockTransport(handler)


# ------------------------------------------------------------------------ fake llm


class FakeLLM:
    """Canned Pydantic objects, one per schema. Never decides anything."""

    def __init__(self, *, solver_agrees: bool = True, with_diagram: bool = False) -> None:
        self.solver_agrees = solver_agrees
        self.with_diagram = with_diagram
        self.calls: list[tuple[str, str | None]] = []
        self.prompts: list[tuple[str, str]] = []
        self.last_item: ProbeItem | None = None
        self.n = 0

    def __call__(self, prompt, schema, provider_cfg=None, **kwargs):
        role = kwargs.get("role")
        self.calls.append((schema.__name__, role))
        self.prompts.append((schema.__name__, str(prompt)))
        meta = SimpleNamespace(
            provider="fake", model="fake", prompt_hash="0" * 8, latency_ms=0, cached=False
        )
        return self._build(prompt, schema), meta

    def _build(self, prompt, schema):
        if schema is PlanGraph:
            return PlanGraph.model_validate(
                {
                    "nodes": [
                        {"title": "Vector spaces", "aliases": ["linear spaces"]},
                        {"title": "Covectors", "aliases": ["1-forms"]},
                        {"title": "Wedge product", "aliases": ["exterior product"]},
                        {"title": "k-forms", "aliases": []},
                    ],
                    "edges": [
                        {
                            "from": "Vector spaces",
                            "to": "Covectors",
                            "type": "strict_prerequisite",
                            "provenance": "model",
                        },
                        {
                            "from": "Covectors",
                            "to": "Wedge product",
                            "type": "course_sequence",
                            "provenance": "course",
                        },
                        {
                            "from": "Wedge product",
                            "to": "k-forms",
                            "type": "strict_prerequisite",
                            "provenance": "model",
                        },
                    ],
                }
            )
        if schema is SolverVerdict:
            key = self.last_item.answer if self.last_item else "B"
            return SolverVerdict(
                answer=key if self.solver_agrees else "D",
                reasoning="because",
                ambiguous=not self.solver_agrees,
            )
        if isinstance(schema, type) and issubclass(schema, ProbeItem):
            self.n += 1
            item = schema(
                stem=f"Question {self.n}: which statement is right?",
                options=[
                    "A. the sideways-vector answer",
                    "B. the linear-map answer",
                    "C. the component-sum answer",
                    "D. the coefficient answer",
                    "E. I do not know",
                ],
                answer="B",
                distractor_misconceptions={
                    "A": "a covector is just a vector written sideways",
                    "C": "a covector sums the vector's components",
                    "D": "a covector reads off a single coordinate",
                },
                kind="apply",
                components=["dual-space"],
                explanation="A covector is a linear functional.",
            )
            self.last_item = item
            return item
        if schema is TeachStep:
            return TeachStep(
                strategy="example-first",
                markdown="Concrete first: height above the floor eats an arrow.",
                mermaid="flowchart LR\n  A[v] --> B[number]" if self.with_diagram else None,
                latex="\\alpha(v) = v^3",
                self_explanation_prompt="Why does linearity have to be in the definition?",
            )
        if schema is HintText:
            return HintText(level=1, hint_markdown="You have what you need — take a shot.")
        if schema is InterruptAnswer:
            # Deliberately a sentence the ingested source can be cited for.
            return InterruptAnswer(
                answer_markdown=(
                    "A covector is a linear map from a vector space to the reals.\n\n"
                    "That is why the output is a number: it is in the definition, not a "
                    "consequence of the row-vector picture."
                )
            )
        if schema is TeachBackGrade:
            return TeachBackGrade(
                score=2, anchor="2 - Correct but bounded", feedback_markdown="Mechanism right."
            )
        if schema is MisconceptionProbe:
            return MisconceptionProbe(
                claim="a covector is a vector written sideways",
                step="prediction",
                prompt_markdown="Different setup: what do you expect, and why?",
            )
        if schema is MisconceptionStepOutcome:
            return MisconceptionStepOutcome(
                outcome="held", reason="matches the claim", reply_markdown="Walk me through it."
            )
        if schema is MermaidFix:
            return MermaidFix(mermaid="flowchart LR\n  A[fixed] --> B[ok]")
        if schema is Feedback:
            return Feedback(feedback_markdown="Not quite — here is what to look at.")
        raise AssertionError(f"FakeLLM has no canned answer for {schema.__name__}")


# ------------------------------------------------------------------------ fixtures


@pytest.fixture(autouse=True)
def _gateway_env(monkeypatch):
    for var in (
        "LT_LEARNER_URL",
        "LT_RENDER_URL",
        "LT_WEB_DIR",
        "LT_PROMPT_PACK_DIR",
        "LT_PROMPT_VERSION",
        "LT_PACE_MIN",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("LT_EMBED_MODEL", "hash")


@pytest.fixture(autouse=True)
def _no_real_ollama(monkeypatch):
    """Health's model-presence probe must never reach a real Ollama from the suite."""

    def unreachable(base_url, timeout=None):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(ops_mod, "ollama_models", unreachable)


@pytest.fixture
def llm(monkeypatch) -> FakeLLM:
    fake = FakeLLM()
    monkeypatch.setattr(generate_mod, "generate_structured", fake)
    return fake


def build_client(settings, *, render=None) -> TestClient:
    app = create_app(
        settings,
        learner_transport=SyncASGITransport(create_learner_app(settings)),
        learner_url="http://learner-svc",
        render_transport=render,
        render_url="http://render-svc" if render is not None else None,
    )
    return TestClient(app)


@pytest.fixture
def client(settings, llm) -> TestClient:
    with build_client(settings) as test_client:
        yield test_client


def create_goal(client: TestClient) -> dict:
    response = client.post(
        "/api/goals",
        json={
            "goal_id": GOAL,
            "title": "Differential forms",
            "concept": "Differential forms on manifolds",
            "depth": "apply",
            "purpose": "Read Spivak ch.4",
            "deadline": "2026-11-01",
            "minutes_per_session": 45,
            "sessions_per_week": 3,
            "assessment": "problem sets",
            "source_priority": "authority",
            "domain": "math-cs",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def ingest_source(client: TestClient) -> None:
    response = client.post(
        f"/api/corpus/{GOAL}/ingest",
        json={
            "text": MARKDOWN_SOURCE,
            "role": "alignment",
            "title": "Course notes",
            "kind": "md",
        },
    )
    assert response.status_code == 200, response.text


# --------------------------------------------------------------------------- tests


def test_create_goal_returns_the_contract_shape(client):
    body = create_goal(client)
    assert body["phase"] == "grounding"
    assert body["goal"]["goal_id"] == GOAL
    assert body["goal"]["domain"] == "math-cs"  # a contract-only field, merged back in
    assert Path(body["sources_dir"]).is_dir()


def test_goal_id_is_slugified_when_omitted(client):
    body = client.post(
        "/api/goals", json={"title": "Linear Algebra: Done Right", "minutes_per_session": 30}
    ).json()
    assert body["goal"]["goal_id"] == "linear-algebra-done-right"


def test_unknown_goal_is_a_404_in_the_gateway_envelope(client):
    response = client.get("/api/goals/nope")
    assert response.status_code == 404
    assert set(response.json()) == {"error", "code"}
    assert response.json()["code"] == "not_found"


def test_plan_uses_the_course_structure_as_a_prior_and_cites_or_abstains(client):
    create_goal(client)
    ingest_source(client)

    response = client.post(f"/api/goals/{GOAL}/plan")
    assert response.status_code == 200, response.text
    plan = response.json()

    assert plan["course_prior_used"] is True
    assert len(plan["nodes"]) == 4
    assert "graph TD" in plan["mermaid"]
    assert plan["graph_version"] >= 1

    statuses = {row["status"] for row in plan["verification"]}
    assert statuses <= {"cited", "abstain"}
    assert any(row["status"] == "cited" for row in plan["verification"])

    feasibility = plan["feasibility"]
    assert feasibility["pace_min"] == 12
    assert "assumption" in feasibility["statement"]


def test_plan_without_a_corpus_reports_no_prior_and_no_verification(client):
    create_goal(client)
    plan = client.post(f"/api/goals/{GOAL}/plan").json()
    assert plan["verification"] == []
    # course_sequence edges from the model still count as a course prior
    assert plan["course_prior_used"] is True


def test_the_probe_refuses_to_start_on_an_unreviewed_graph(client):
    create_goal(client)
    client.post(f"/api/goals/{GOAL}/plan")
    response = client.post(f"/api/goals/{GOAL}/probe/start", json={})
    assert response.status_code == 400
    assert response.json()["code"] == "plan_not_approved"


def test_full_flow(client, settings):
    create_goal(client)
    ingest_source(client)
    client.post(f"/api/goals/{GOAL}/plan")
    approved = client.post(f"/api/goals/{GOAL}/plan/approve", json={"ops": []})
    assert approved.status_code == 200

    assert client.get(f"/api/goals/{GOAL}").json()["phase"] == "probe"

    # --- probe -----------------------------------------------------------
    started = client.post(f"/api/goals/{GOAL}/probe/start", json={"channel": "web"}).json()
    session_id = started["session_id"]
    assert started["budget"] == 12
    assert started["done"] is False
    question = started["question"]
    assert question["allow_idk"] is True
    assert [o["key"] for o in question["options"]] == ["A", "B", "C", "D", "E"]
    assert "answer" not in question  # the key never leaves the gateway

    for index in range(3):
        answered = client.post(
            f"/api/goals/{GOAL}/probe/answer",
            json={
                "session_id": session_id,
                "item_id": question["item_id"],
                "response": "A",  # wrong; the key is B
                "confidence": 2,
                "correct": True,  # a lying client
            },
        )
        assert answered.status_code == 200, answered.text
        body = answered.json()
        assert body["recorded"]["correct"] is False
        assert body["asked"] == index + 1
        if body["next"]:
            question = body["next"]
    assert body["done"] is True
    assert "consecutive wrong" in body["stop_reason"]

    # --- teach -----------------------------------------------------------
    step = client.post(f"/api/goals/{GOAL}/teach/next", json={"session_id": session_id})
    assert step.status_code == 200, step.text
    taught = step.json()
    assert taught["assistance_level"] == 0
    assert taught["step"]["strategy"] == "example-first"  # novice -> worked example
    assert taught["step"]["latex_ok"] is True
    checkpoint = taught["checkpoint"]

    # the reveal before an attempt is refused
    refused = client.post(
        f"/api/goals/{GOAL}/teach/hint",
        json={"session_id": session_id, "item_id": checkpoint["item_id"], "level": 6},
    )
    assert refused.status_code == 409
    assert refused.json()["code"] == "hint_refused"

    # wrong, at high confidence -> a misconception is suspected, not asserted
    wrong = client.post(
        f"/api/goals/{GOAL}/teach/answer",
        json={
            "session_id": session_id,
            "item_id": checkpoint["item_id"],
            "response": "A",
            "confidence": 5,
            "assistance_level": 0,
            "correct": True,  # ignored
        },
    ).json()
    assert wrong["correct"] is False
    assert wrong["decision"] == "repeat"
    assert wrong["reveal_allowed"] is True
    assert wrong["misconception_suspected"]["claim"] == (
        "a covector is just a vector written sideways"
    )

    # one confirmation step: still a hypothesis
    stepped = client.post(
        f"/api/goals/{GOAL}/misconception/step",
        json={
            "session_id": session_id,
            "node_id": checkpoint["node_id"],
            "claim": wrong["misconception_suspected"]["claim"],
            "step": "reasoning",
            "learner_response": "I thought it was the same thing written sideways",
        },
    ).json()
    assert stepped["state"] == "suspected"
    assert stepped["next_step"] == "prediction"

    # now a hint is allowed, one level at a time
    hinted = client.post(
        f"/api/goals/{GOAL}/teach/hint",
        json={"session_id": session_id, "item_id": checkpoint["item_id"], "level": 1},
    )
    assert hinted.status_code == 200
    assert hinted.json()["level"] == 1

    right = client.post(
        f"/api/goals/{GOAL}/teach/answer",
        json={
            "session_id": session_id,
            "item_id": checkpoint["item_id"],
            "response": "B",
            "confidence": 4,
            "assistance_level": 1,
        },
    ).json()
    assert right["correct"] is True
    assert right["assistance_level"] == 1
    assert right["decision"] in ("continue", "teach_back_due")
    assert right["node_state"]["state"] in ("unknown", "fragile", "known", "misconception")

    # --- teach-back ------------------------------------------------------
    graded = client.post(
        f"/api/goals/{GOAL}/teach/teach-back",
        json={
            "session_id": session_id,
            "node_id": checkpoint["node_id"],
            "explanation": "A covector eats a vector and returns a number, linearly.",
        },
    ).json()
    assert graded["score"] == 2
    assert graded["rubric_version"] == "teach-back-v1"
    assert graded["recorded"]["kind"] == "teach_back"

    # --- session end -----------------------------------------------------
    ended = client.post(
        f"/api/goals/{GOAL}/session/end",
        json={"session_id": session_id, "summary": "covectors, one assisted pass"},
    ).json()
    log_path = Path(ended["log_path"])
    assert log_path.exists()
    log = log_path.read_text(encoding="utf-8")
    assert "prompt_version: teach/v1" in log
    assert "rubric_version: teach-back-v1" in log
    assert "## Teach-back" in log
    assert log_path.parent == Path(settings.sessions_dir)

    # --- the two views ---------------------------------------------------
    the_map = client.get(f"/api/goals/{GOAL}/map").json()
    assert "graph TD" in the_map["curriculum"]["mermaid"]
    assert "graph LR" in the_map["path"]["mermaid"]
    assert len(the_map["path"]["order"]) == 4
    assert set(the_map["states"]) == {n["node_id"] for n in the_map["curriculum"]["nodes"]}

    receipts = client.get(f"/api/goals/{GOAL}/receipts/{checkpoint['node_id']}").json()
    assert receipts["node"]["node_id"] == checkpoint["node_id"]
    assert receipts["why"], "the state must come with the reasons that produced it"
    assert len(receipts["evidence"]) >= 2
    assert receipts["misconceptions"][0]["claim"] == (
        "a covector is just a vector written sideways"
    )
    # every part of a receipt is learner-svc's; the gateway never opens events.db
    assert receipts["evidence_source"] == "learner-svc GET /v1/events"
    assert receipts["evidence"][0]["node_id"] == checkpoint["node_id"]
    assert "note" not in receipts

    # --- passport --------------------------------------------------------
    passport = client.get("/api/passport")
    assert passport.status_code == 200
    assert passport.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(passport.content)) as archive:
        names = set(archive.namelist())
        for member in (
            "events.jsonl",
            "state.json",
            "learner.md",
            "notes.md",
            "goals.json",
            "graph.json",
            "disputes.json",
            "MANIFEST.json",
            "gateway-state.json",
        ):
            assert member in names, member
        assert any(name.startswith("artifacts/") for name in names)
        assert archive.read("events.jsonl").decode().strip()
        assert json.loads(archive.read("goals.json"))["goals"][0]["goal_id"] == GOAL
        assert any(name.startswith("artifacts/sessions/") for name in names), (
            "the session log written at session end travels with the export"
        )
        # the zip is learner-svc's, wrapped: its manifest is kept, not overwritten
        manifest = json.loads(archive.read("MANIFEST.json"))
        assert manifest["source"] == "learner-svc GET /v1/passport"
        assert manifest["learner_svc"]["generated_by"] == "learner-svc"
        assert "gateway-state.json" in manifest["added"]
        assert json.loads(archive.read("gateway-state.json"))["goals"][GOAL]["phase"]

    # --- health ----------------------------------------------------------
    health = client.get("/api/health").json()
    assert set(health["services"]) == {"learner", "render", "llm", "corpus"}
    assert health["services"]["learner"] == "ok"
    assert health["services"]["corpus"] == "ok"
    assert health["services"]["render"] == "unconfigured"  # LT_RENDER_URL unset
    assert health["prompt_version"] == "teach/v1"


def test_receipts_and_the_passport_have_no_second_path_into_the_database(settings, llm):
    """Both used to read ``events.db`` directly when learner-svc could not answer.

    That fallback is gone: they are learner-svc reads, and an unreachable learner-svc is a
    502 — the honest answer — not a receipt with an empty evidence list or a passport that
    is quietly short.
    """

    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("learner-svc is not running", request=request)

    app = create_app(
        settings,
        learner_transport=httpx.MockTransport(refuse),
        learner_url="http://learner-svc",
    )
    with TestClient(app) as client:
        passport = client.get("/api/passport")
        assert passport.status_code == 502
        assert passport.json()["code"] == "learner_down"
        assert client.get(f"/api/goals/{GOAL}/receipts/n_anything").status_code == 502


def test_the_session_log_is_written_by_learner_svc(client, settings):
    """The gateway hands the markdown to the service that owns the vault, and the log
    lands with the same ``note`` event ``learner log`` appends."""

    create_goal(client)
    client.post(f"/api/goals/{GOAL}/plan")
    client.post(f"/api/goals/{GOAL}/plan/approve", json={})
    session_id = client.post(f"/api/goals/{GOAL}/probe/start", json={}).json()["session_id"]

    ended = client.post(
        f"/api/goals/{GOAL}/session/end", json={"session_id": session_id}
    ).json()
    log_path = Path(ended["log_path"])
    assert log_path.exists()
    assert log_path.parent == Path(settings.sessions_dir)

    from learning_tutor.learner import api
    from learning_tutor.learner.store import open_store

    with open_store(settings) as store:
        notes = api.events(store, kind="note")["events"]
    assert notes[0]["payload"]["log"] == str(log_path)


def test_the_server_side_key_decides_not_the_client(client):
    """The contract's answer routes take a `correct` flag. It is never read."""

    create_goal(client)
    client.post(f"/api/goals/{GOAL}/plan")
    client.post(f"/api/goals/{GOAL}/plan/approve", json={})
    started = client.post(f"/api/goals/{GOAL}/probe/start", json={}).json()
    item_id = started["question"]["item_id"]

    lying = client.post(
        f"/api/goals/{GOAL}/probe/answer",
        json={
            "session_id": started["session_id"],
            "item_id": item_id,
            "response": "C",
            "correct": True,
        },
    ).json()
    assert lying["recorded"]["correct"] is False

    honest = client.post(
        f"/api/goals/{GOAL}/probe/answer",
        json={
            "session_id": started["session_id"],
            "item_id": item_id,
            "response": "B",
        },
    ).json()
    assert honest["recorded"]["correct"] is True


def test_an_item_with_no_stored_key_is_refused_rather_than_guessed(client):
    create_goal(client)
    client.post(f"/api/goals/{GOAL}/plan")
    client.post(f"/api/goals/{GOAL}/plan/approve", json={})
    started = client.post(f"/api/goals/{GOAL}/probe/start", json={}).json()
    response = client.post(
        f"/api/goals/{GOAL}/probe/answer",
        json={"session_id": started["session_id"], "item_id": "i_made_up", "response": "B"},
    )
    assert response.status_code == 409
    assert response.json()["code"] == "unknown_item"


def test_a_blind_solver_agreement_promotes_the_item_to_practice_evidence(client, settings):
    from learning_tutor.learner.store import open_store

    create_goal(client)
    client.post(f"/api/goals/{GOAL}/plan")
    client.post(f"/api/goals/{GOAL}/plan/approve", json={})
    started = client.post(f"/api/goals/{GOAL}/probe/start", json={}).json()

    with open_store(settings) as store:
        rows = [dict(r) for r in store.query("SELECT * FROM items")]
        validations = [dict(r) for r in store.query("SELECT * FROM item_validations")]
    assert rows[0]["status"] == "PRACTICE_EVIDENCE"
    assert validations[0]["evaluation_method"] == "blind_solver"
    assert started["question"]["item_id"] == rows[0]["item_id"]


def test_a_solver_disagreement_leaves_the_item_teaching_only(settings, monkeypatch):
    from learning_tutor.learner.store import open_store

    monkeypatch.setattr(generate_mod, "generate_structured", FakeLLM(solver_agrees=False))
    with build_client(settings) as client:
        create_goal(client)
        client.post(f"/api/goals/{GOAL}/plan")
        client.post(f"/api/goals/{GOAL}/plan/approve", json={})
        client.post(f"/api/goals/{GOAL}/probe/start", json={})

    with open_store(settings) as store:
        rows = [dict(r) for r in store.query("SELECT * FROM items")]
    assert rows, "the item is still created — it can teach, it just cannot be evidence"
    assert {row["status"] for row in rows} == {"TEACHING_ONLY"}


def test_a_same_model_solver_leaves_the_item_teaching_only(settings, monkeypatch):
    """`llm` refuses a solver that is the tutor. That is a correct outcome, not a crash."""

    from learning_tutor.learner.store import open_store
    from learning_tutor.llm.structured import SameSolverError

    fake = FakeLLM()

    def guarded(prompt, schema, provider_cfg=None, **kwargs):
        if kwargs.get("role") == "solver":
            raise SameSolverError("solver and tutor both resolve to ollama/llama3.2")
        return fake(prompt, schema, provider_cfg, **kwargs)

    monkeypatch.setattr(generate_mod, "generate_structured", guarded)
    with build_client(settings) as client:
        create_goal(client)
        client.post(f"/api/goals/{GOAL}/plan")
        client.post(f"/api/goals/{GOAL}/plan/approve", json={})
        client.post(f"/api/goals/{GOAL}/probe/start", json={})

    with open_store(settings) as store:
        rows = [dict(r) for r in store.query("SELECT * FROM items")]
    assert {row["status"] for row in rows} == {"TEACHING_ONLY"}


def test_the_diagram_loop_checks_then_renders(settings, monkeypatch):
    monkeypatch.setattr(generate_mod, "generate_structured", FakeLLM(with_diagram=True))
    with build_client(settings, render=render_transport(ok=True)) as client:
        create_goal(client)
        client.post(f"/api/goals/{GOAL}/plan")
        client.post(f"/api/goals/{GOAL}/plan/approve", json={})
        started = client.post(f"/api/goals/{GOAL}/probe/start", json={}).json()
        taught = client.post(
            f"/api/goals/{GOAL}/teach/next", json={"session_id": started["session_id"]}
        ).json()
        assert taught["step"]["svg"] == "<svg>ok</svg>"
        assert taught["step"]["latex_ok"] is True
        assert client.get("/api/health").json()["services"]["render"] == "ok"


def test_a_broken_diagram_is_fixed_once_then_rendered(settings, monkeypatch):
    monkeypatch.setattr(generate_mod, "generate_structured", FakeLLM(with_diagram=True))
    with build_client(settings, render=render_transport(ok=False)) as client:
        create_goal(client)
        client.post(f"/api/goals/{GOAL}/plan")
        client.post(f"/api/goals/{GOAL}/plan/approve", json={})
        started = client.post(f"/api/goals/{GOAL}/probe/start", json={}).json()
        taught = client.post(
            f"/api/goals/{GOAL}/teach/next", json={"session_id": started["session_id"]}
        ).json()
        assert "fixed" in taught["step"]["mermaid"]
        assert taught["step"]["svg"] == "<svg>ok</svg>"


def test_render_proxy_rejects_a_diagram_that_does_not_parse(settings, llm):
    with build_client(settings, render=render_transport(ok=False)) as client:
        response = client.post("/api/render", json={"mermaid": "flowchart TD\n  A[[["})
        assert response.status_code == 400
        assert response.json()["code"] == "bad_mermaid"


def test_render_proxy_says_unconfigured_rather_than_pretending(client):
    response = client.post("/api/render", json={"mermaid": "flowchart TD\n  A --> B"})
    assert response.status_code == 502
    assert response.json()["code"] == "render_unconfigured"


def test_learner_svc_down_is_a_502_naming_the_service(settings, llm):
    app = create_app(settings, learner_url="http://127.0.0.1:1")
    with TestClient(app) as client:
        response = client.get("/api/goals")
        assert response.status_code == 502
        assert response.json()["code"] == "learner_down"
        health = client.get("/api/health").json()
        assert health["services"]["learner"] == "down"
        assert health["ok"] is False


def _llm_env(monkeypatch, *, provider="ollama", model="llama3.1:8b", solver="gemma3:12b"):
    monkeypatch.setenv("LT_LLM_PROVIDER", provider)
    monkeypatch.setenv("LT_LLM_MODEL", model)
    monkeypatch.setenv("LT_LLM_SOLVER_PROVIDER", provider)
    monkeypatch.setenv("LT_LLM_SOLVER_MODEL", solver)


def test_health_reports_both_models_present(client, monkeypatch):
    _llm_env(monkeypatch)
    calls: list[str] = []

    def tags(base_url, timeout=None):
        calls.append(base_url)
        return {"llama3.1:8b", "gemma3:12b", "nomic-embed-text:latest"}

    monkeypatch.setattr(ops_mod, "ollama_models", tags)
    health = client.get("/api/health").json()
    llm = health["detail"]["llm"]
    assert health["services"]["llm"] == "ok"
    assert llm["provider"] == "ollama"
    assert llm["model"] == "llama3.1:8b"
    assert llm["solver_model"] == "gemma3:12b"
    assert llm["model_present"] is True
    assert llm["solver_present"] is True
    assert "error" not in llm
    assert len(calls) == 1, "one tags call covers both roles on the same server"


def test_health_reports_a_missing_model_without_failing(client, monkeypatch):
    _llm_env(monkeypatch, model="llama3.2", solver="gemma3:12b")
    monkeypatch.setattr(ops_mod, "ollama_models", lambda url, timeout=None: {"gemma3:12b"})
    health = client.get("/api/health").json()
    llm = health["detail"]["llm"]
    assert llm["model_present"] is False
    assert llm["solver_present"] is True
    assert health["services"]["llm"] == "unconfigured"
    assert health["ok"] is True, "a missing model is a setup problem, not a crash"


def test_health_untagged_model_matches_its_latest_tag(client, monkeypatch):
    _llm_env(monkeypatch, model="llama3.1", solver="gemma3:12b")
    monkeypatch.setattr(
        ops_mod, "ollama_models", lambda url, timeout=None: {"llama3.1:latest", "gemma3:12b"}
    )
    assert client.get("/api/health").json()["detail"]["llm"]["model_present"] is True


def test_health_when_ollama_is_unreachable_says_unknown_not_missing(client, monkeypatch):
    _llm_env(monkeypatch)
    # the autouse fixture already makes ollama_models raise ConnectError
    health = client.get("/api/health").json()
    llm = health["detail"]["llm"]
    assert llm["model_present"] is None
    assert llm["solver_present"] is None
    assert "ConnectError" in llm["error"]
    assert health["services"]["llm"] == "degraded"
    assert health["ok"] is True, "the model-free tools still work without Ollama"


def test_health_is_ok_again_once_ollama_answers(client, monkeypatch):
    _llm_env(monkeypatch)
    assert client.get("/api/health").json()["services"]["llm"] == "degraded"
    monkeypatch.setattr(
        ops_mod, "ollama_models", lambda url, timeout=None: {"llama3.1:8b", "gemma3:12b"}
    )
    assert client.get("/api/health").json()["services"]["llm"] == "ok"


def test_health_skips_the_presence_probe_for_non_ollama_providers(client, monkeypatch):
    _llm_env(monkeypatch, provider="claude", model="claude-x", solver="claude-y")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")

    def boom(base_url, timeout=None):
        raise AssertionError("tags must not be queried for claude")

    monkeypatch.setattr(ops_mod, "ollama_models", boom)
    llm = client.get("/api/health").json()["detail"]["llm"]
    assert llm["model_present"] is None
    assert llm["solver_present"] is None
    assert "error" not in llm


def test_idempotency_key_is_passed_through_to_learner_svc(client):
    create_goal(client)
    first = client.post(
        f"/api/goals/{GOAL}/plan", headers={"Idempotency-Key": "plan-1"}
    ).json()
    second = client.post(
        f"/api/goals/{GOAL}/plan", headers={"Idempotency-Key": "plan-1"}
    ).json()
    # the graph import replayed rather than doubling the node count
    assert first["graph_version"] == second["graph_version"]
    assert len(second["nodes"]) == 4


def test_goal_listing_carries_the_phase_and_the_four_colours(client):
    create_goal(client)
    client.post(f"/api/goals/{GOAL}/plan")
    listing = client.get("/api/goals").json()["goals"]
    row = next(g for g in listing if g["goal_id"] == GOAL)
    assert row["phase"] == "plan"
    assert row["node_count"] == 4
    assert row["unknown"] == 4
    assert row["known"] == row["fragile"] == row["misconception"] == 0


def test_dispute_of_i_already_know_this_comes_with_two_items(client):
    create_goal(client)
    plan = client.post(f"/api/goals/{GOAL}/plan").json()
    client.post(f"/api/goals/{GOAL}/plan/approve", json={})
    node_id = plan["nodes"][0]["node_id"]
    response = client.post(
        f"/api/goals/{GOAL}/dispute",
        json={
            "type": "I already know this",
            "node_id": node_id,
            "note": "I did this in undergrad",
        },
    ).json()
    assert response["dispute_id"]
    assert len(response["check"]["items"]) == 2
    assert "assistance 0" in response["check"]["rule"]


def test_the_second_dispute_item_asks_for_a_different_surface_form(client, llm):
    create_goal(client)
    plan = client.post(f"/api/goals/{GOAL}/plan").json()
    client.post(f"/api/goals/{GOAL}/plan/approve", json={})
    before = len(llm.prompts)
    client.post(
        f"/api/goals/{GOAL}/dispute",
        json={"type": "I already know this", "node_id": plan["nodes"][0]["node_id"]},
    )
    item_prompts = [text for name, text in llm.prompts[before:] if name == "ProbeItem"]
    assert len(item_prompts) == 2
    assert "different surface form" not in item_prompts[0]
    assert "different surface form" in item_prompts[1]


def test_a_scope_dispute_gets_no_check_items(client):
    create_goal(client)
    plan = client.post(f"/api/goals/{GOAL}/plan").json()
    response = client.post(
        f"/api/goals/{GOAL}/dispute",
        json={"type": "not on my exam", "node_id": plan["nodes"][0]["node_id"]},
    ).json()
    assert response["check"] is None


def test_the_index_page_links_the_docs_when_no_web_ui_is_mounted(client):
    body = client.get("/").text
    assert "/docs" in body


def test_the_mounted_routers_are_where_the_contract_puts_them(client):
    assert client.get("/api/llm/health").status_code == 200
    assert client.get(f"/api/corpus/{GOAL}/sources").status_code == 200


def test_state_survives_a_restart(settings, llm):
    with build_client(settings) as client:
        create_goal(client)
        client.post(f"/api/goals/{GOAL}/plan")
        client.post(f"/api/goals/{GOAL}/plan/approve", json={})
        started = client.post(f"/api/goals/{GOAL}/probe/start", json={}).json()
        item_id = started["question"]["item_id"]

    state_file = Path(settings.data_dir) / "gateway" / "state.json"
    assert state_file.exists()

    with build_client(settings) as client:
        assert client.get(f"/api/goals/{GOAL}").json()["phase"] == "probe"
        answered = client.post(
            f"/api/goals/{GOAL}/probe/answer",
            json={"session_id": started["session_id"], "item_id": item_id, "response": "B"},
        )
        assert answered.status_code == 200
        assert answered.json()["recorded"]["correct"] is True


# ------------------------------------------------------------------------ interrupts


def teach_one_step(client: TestClient) -> tuple[str, dict]:
    """Walk a goal to a live teach step. Returns ``(session_id, teach/next body)``."""

    create_goal(client)
    client.post(f"/api/goals/{GOAL}/plan")
    client.post(f"/api/goals/{GOAL}/plan/approve", json={})
    session_id = client.post(f"/api/goals/{GOAL}/probe/start", json={}).json()["session_id"]
    taught = client.post(f"/api/goals/{GOAL}/teach/next", json={"session_id": session_id})
    assert taught.status_code == 200, taught.text
    return session_id, taught.json()


def read_state(settings) -> dict:
    return json.loads(
        (Path(settings.data_dir) / "gateway" / "state.json").read_text(encoding="utf-8")
    )["goals"][GOAL]


def test_an_interrupt_is_answered_inline_and_cited_when_the_corpus_supports_it(client):
    create_goal(client)
    ingest_source(client)
    client.post(f"/api/goals/{GOAL}/plan")
    client.post(f"/api/goals/{GOAL}/plan/approve", json={})
    session_id = client.post(f"/api/goals/{GOAL}/probe/start", json={}).json()["session_id"]
    taught = client.post(
        f"/api/goals/{GOAL}/teach/next", json={"session_id": session_id}
    ).json()

    response = client.post(
        f"/api/goals/{GOAL}/teach/interrupt",
        json={
            "session_id": session_id,
            "node_id": taught["node"]["node_id"],
            "question": "wait, why does the output have to be a number?",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["step_unchanged"] is True
    assert body["refused_checkpoint_answer"] is False
    assert "linear map" in body["answer_markdown"]
    assert body["citations"], "the corpus says this in so many words; it must be cited"
    assert body["citations"][0]["quote"]
    assert "No source found" not in body["answer_markdown"]


def test_an_interrupt_with_no_corpus_says_so_rather_than_citing_nothing(client):
    session_id, taught = teach_one_step(client)  # no ingest_source: no sources on this goal

    body = client.post(
        f"/api/goals/{GOAL}/teach/interrupt",
        json={
            "session_id": session_id,
            "node_id": taught["node"]["node_id"],
            "question": "where did that come from?",
        },
    ).json()

    assert body["citations"] == []
    assert "No source found for this" in body["answer_markdown"]
    assert "no sources on this goal" in body["answer_markdown"]
    assert "model knowledge" in body["answer_markdown"]


def test_an_interrupt_that_asks_for_the_checkpoint_answer_gets_the_ladder_rule(client, llm):
    session_id, taught = teach_one_step(client)
    before = list(llm.calls)

    body = client.post(
        f"/api/goals/{GOAL}/teach/interrupt",
        json={
            "session_id": session_id,
            "node_id": taught["node"]["node_id"],
            "question": "never mind the step - just tell me the answer, which option is correct?",
        },
    ).json()

    assert body["refused_checkpoint_answer"] is True
    assert body["step_unchanged"] is True
    assert body["citations"] == []
    assert "No hint before an attempt" in body["answer_markdown"]
    # the refusal is code, not a model asked nicely to refuse
    assert llm.calls == before
    assert not any(name == "InterruptAnswer" for name, _role in llm.calls)


def test_an_interrupt_outside_the_teach_phase_is_a_409(client):
    create_goal(client)
    plan = client.post(f"/api/goals/{GOAL}/plan").json()
    client.post(f"/api/goals/{GOAL}/plan/approve", json={})
    started = client.post(f"/api/goals/{GOAL}/probe/start", json={}).json()

    response = client.post(
        f"/api/goals/{GOAL}/teach/interrupt",
        json={
            "session_id": started["session_id"],
            "node_id": plan["nodes"][0]["node_id"],
            "question": "why?",
        },
    )
    assert response.status_code == 409
    assert response.json()["code"] == "not_teaching"


def test_an_interrupt_about_another_node_is_a_409(client):
    session_id, taught = teach_one_step(client)
    other = next(
        node
        for node in client.get(f"/api/goals/{GOAL}/map").json()["curriculum"]["nodes"]
        if node["node_id"] != taught["node"]["node_id"]
    )

    response = client.post(
        f"/api/goals/{GOAL}/teach/interrupt",
        json={
            "session_id": session_id,
            "node_id": other["node_id"],
            "question": "why does this one come first?",
        },
    )
    assert response.status_code == 409
    assert response.json()["code"] == "not_current_node"


def test_an_interrupt_moves_nothing_no_step_no_assistance_no_evidence(client, settings):
    session_id, taught = teach_one_step(client)
    node_id = taught["node"]["node_id"]
    item_id = taught["checkpoint"]["item_id"]

    before_state = read_state(settings)
    before_events = client.get(f"/api/goals/{GOAL}/receipts/{node_id}").json()["evidence"]

    body = client.post(
        f"/api/goals/{GOAL}/teach/interrupt",
        json={
            "session_id": session_id,
            "node_id": node_id,
            "question": "why is that step allowed?",
        },
    ).json()
    assert body["step_unchanged"] is True

    after_state = read_state(settings)
    after_events = client.get(f"/api/goals/{GOAL}/receipts/{node_id}").json()["evidence"]

    assert after_state["teach"]["node_id"] == before_state["teach"]["node_id"]
    assert after_state["teach"]["strategy"] == before_state["teach"]["strategy"]
    assert after_state["teach"]["strategy_switches"] == 0
    assert after_state["teach"]["nodes_completed"] == before_state["teach"]["nodes_completed"]
    assert after_state["questions"][item_id]["assistance_level"] == 0
    assert after_state["questions"][item_id]["attempts"] == 0
    assert after_state["in_flight"][session_id] == item_id
    assert len(after_events) == len(before_events), "an interrupt writes no evidence"

    # the step the learner is reading is still the one on file, unchanged
    steps = [s for s in after_state["teach"]["steps"] if s["kind"] == "step"]
    was = [s for s in before_state["teach"]["steps"] if s["kind"] == "step"]
    assert len(steps) == 1
    assert steps[0]["markdown"] == was[0]["markdown"]
    # and the question itself is not swallowed: it is in gateway state (and the passport)
    asked = [s for s in after_state["teach"]["steps"] if s["kind"] == "interrupt"]
    assert asked and asked[0]["question"] == "why is that step allowed?"


# ------------------------------------------------------------------------ feasibility


def test_the_plan_feasibility_carries_the_field_names_the_ui_reads(client):
    create_goal(client)  # deadline 2026-11-01, 45 min per session, 3 sessions/week
    feasibility = client.post(f"/api/goals/{GOAL}/plan").json()["feasibility"]

    assert feasibility is not None
    assert set(feasibility) >= {
        "sessions_needed",
        "sessions_available",
        "verdict",
        "assumption",
    }
    assert feasibility["sessions_needed"] >= 1
    assert feasibility["sessions_available"] == feasibility["sessions_left"]
    assert feasibility["verdict"] in ("comfortable", "tight", "not-feasible", "unknown")
    assert "not a measurement" in feasibility["assumption"]


def test_plan_feasibility_is_null_when_there_is_no_session_budget_to_divide_by(client):
    client.post(
        "/api/goals",
        json={"goal_id": GOAL, "title": "Differential forms", "minutes_per_session": 0},
    )
    plan = client.post(f"/api/goals/{GOAL}/plan").json()
    assert plan["feasibility"] is None


def test_plan_feasibility_without_a_deadline_is_present_but_unknown(client):
    client.post(
        "/api/goals",
        json={"goal_id": GOAL, "title": "Differential forms", "minutes_per_session": 45},
    )
    feasibility = client.post(f"/api/goals/{GOAL}/plan").json()["feasibility"]
    assert feasibility["sessions_available"] is None
    assert feasibility["verdict"] == "unknown"
    assert feasibility["sessions_needed"] >= 1
