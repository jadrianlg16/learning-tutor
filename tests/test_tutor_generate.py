"""The generation half: schema plumbing, the plan payload, the diagram loop.

No model here either — every function under test either shapes data or drives the render
client, and the render client is a stand-in.
"""

from __future__ import annotations

import pytest

from learning_tutor.tutor.generate import (
    MermaidFix,
    PlanGraph,
    ProbeItem,
    check_latex,
    goal_block,
    render_diagram,
)
from learning_tutor.tutor.prompts import load_pack

# ------------------------------------------------------------------- plan payload


def _graph(nodes, edges) -> PlanGraph:
    return PlanGraph.model_validate({"nodes": nodes, "edges": edges})


def test_payload_uses_the_wire_name_from():
    graph = _graph(
        [{"title": "A"}, {"title": "B"}],
        [{"from": "A", "to": "B", "type": "strict_prerequisite", "provenance": "model"}],
    )
    payload, dropped = graph.payload()
    assert payload["edges"][0]["from"] == "A"
    assert dropped == []


def test_an_edge_to_a_node_that_was_never_declared_is_dropped_not_fatal():
    """Observed in the first real run against llama3.1:8b: an edge endpoint the model
    never declared as a node made learner-svc 404 the whole import."""

    graph = _graph(
        [{"title": "A"}, {"title": "B"}],
        [
            {"from": "A", "to": "B", "type": "strict_prerequisite", "provenance": "model"},
            {
                "from": "Misconceptions about growth",
                "to": "B",
                "type": "misconception_for",
                "provenance": "model",
            },
        ],
    )
    payload, dropped = graph.payload()
    assert len(payload["edges"]) == 1
    assert len(dropped) == 1
    assert "no such node: Misconceptions about growth" in dropped[0]["reason"]


def test_an_edge_endpoint_may_be_an_alias():
    graph = _graph(
        [{"title": "Covectors", "aliases": ["1-forms"]}, {"title": "Wedge product"}],
        [{"from": "1-forms", "to": "Wedge product", "type": "supports", "provenance": "model"}],
    )
    payload, dropped = graph.payload()
    assert dropped == []
    assert len(payload["edges"]) == 1


def test_duplicate_and_empty_node_titles_are_dropped():
    graph = _graph([{"title": "A"}, {"title": "a"}, {"title": "  "}, {"title": "B"}], [])
    payload, _ = graph.payload()
    assert [n["title"] for n in payload["nodes"]] == ["A", "B"]


def test_item_spec_is_the_shape_learner_svc_takes():
    item = ProbeItem(
        stem="s",
        options=["A. x", "B. y", "E. I do not know"],
        answer="B",
        distractor_misconceptions={"A": "believes x"},
        kind="apply",
        components=["c"],
        explanation="because",
    )
    spec = item.spec()
    assert set(spec) == {
        "stem",
        "options",
        "answer",
        "distractor_misconceptions",
        "kind",
        "components",
    }
    assert "explanation" not in spec  # ours, not the core's


def test_goal_block_omits_empty_fields():
    block = goal_block({"goal_id": "g", "title": "T", "purpose": "", "deadline": None})
    assert "goal_id" in block and "title" in block
    assert "purpose" not in block and "deadline" not in block


# ------------------------------------------------------------------ diagram loop


class FakeRender:
    def __init__(self, *, configured=True, ok=True, ok_after_fix=True, boom=False):
        self.configured = configured
        self.ok = ok
        self.ok_after_fix = ok_after_fix
        self.boom = boom
        self.checks: list[str] = []
        self.renders: list[str] = []

    def check(self, mermaid):
        if self.boom:
            raise RuntimeError("connection refused")
        self.checks.append(mermaid)
        good = self.ok or ("fixed" in mermaid and self.ok_after_fix)
        return {"ok": good, "errors": [] if good else [{"line": 1, "message": "nope"}]}

    def render(self, mermaid, *, theme="default"):
        self.renders.append(mermaid)
        return {"svg": "<svg/>", "warnings": []}

    def latex_check(self, latex, *, html=False):
        return {"ok": "\\frac{1}{" not in latex, "errors": []}


@pytest.fixture
def pack():
    return load_pack()


def test_no_diagram_is_not_an_error():
    result = render_diagram(None, FakeRender())
    assert result == {
        "mermaid": None,
        "svg": None,
        "checked": False,
        "errors": [],
        "fixed": False,
    }


def test_an_unconfigured_render_svc_ships_the_source_unrendered():
    result = render_diagram("flowchart LR\n A-->B", FakeRender(configured=False))
    assert result["mermaid"] == "flowchart LR\n A-->B"
    assert result["svg"] is None
    assert "LT_RENDER_URL" in result["skipped"]


def test_a_good_diagram_is_checked_then_rendered():
    render = FakeRender(ok=True)
    result = render_diagram("flowchart LR\n A-->B", render)
    assert result["svg"] == "<svg/>"
    assert result["fixed"] is False
    assert len(render.checks) == 1 and len(render.renders) == 1


def test_a_broken_diagram_is_fixed_once(monkeypatch, pack):
    from learning_tutor.tutor import generate as generate_mod

    def fake(prompt, schema, provider_cfg=None, **kwargs):
        assert schema is MermaidFix
        return MermaidFix(mermaid="flowchart LR\n fixed-->B"), object()

    monkeypatch.setattr(generate_mod, "generate_structured", fake)
    render = FakeRender(ok=False, ok_after_fix=True)
    result = render_diagram("flowchart LR\n A[[[", render, pack=pack, goal={"domain": "math-cs"})
    assert result["fixed"] is True
    assert result["svg"] == "<svg/>"
    assert len(render.checks) == 2


def test_a_diagram_still_broken_after_one_fix_is_dropped(monkeypatch, pack):
    from learning_tutor.tutor import generate as generate_mod

    monkeypatch.setattr(
        generate_mod,
        "generate_structured",
        lambda *a, **k: (MermaidFix(mermaid="still broken"), object()),
    )
    render = FakeRender(ok=False, ok_after_fix=False)
    result = render_diagram("flowchart LR\n A[[[", render, pack=pack, goal={})
    assert result["mermaid"] is None
    assert result["svg"] is None
    assert "dropped" in result["skipped"]


def test_an_unreachable_render_svc_degrades_rather_than_raising():
    result = render_diagram("flowchart LR\n A-->B", FakeRender(boom=True))
    assert result["svg"] is None
    assert "unreachable" in result["skipped"]


def test_latex_unchecked_and_checked_are_different_claims():
    assert check_latex("x^2", FakeRender(configured=False)) == {
        "ok": True,
        "checked": False,
        "errors": [],
    }
    assert check_latex(None, FakeRender())["checked"] is False
    assert check_latex("x^2", FakeRender()) == {"ok": True, "checked": True, "errors": []}
    assert check_latex("\\frac{1}{", FakeRender())["ok"] is False


# --------------------------------------------------------------- the plan size guard


class FakeLLM:
    """Returns a PlanGraph of a prescribed size, and records every prompt it was given."""

    def __init__(self, *sizes: int) -> None:
        self.sizes = list(sizes)
        self.prompts: list[str] = []

    def __call__(self, prompt, schema, **kwargs):
        self.prompts.append(prompt)
        size = self.sizes[min(len(self.prompts) - 1, len(self.sizes) - 1)]
        return (
            PlanGraph.model_validate(
                {"nodes": [{"title": f"Node {i}"} for i in range(size)], "edges": []}
            ),
            None,
        )


def _plan(monkeypatch, *sizes: int):
    from learning_tutor.tutor import generate as generate_mod

    fake = FakeLLM(*sizes)
    monkeypatch.setattr(generate_mod, "generate_structured", fake)
    plan, _meta = generate_mod.generate_plan(load_pack(), goal={"goal_id": "g", "title": "T"})
    return plan, fake


def test_a_plan_of_the_right_size_is_generated_once(monkeypatch):
    plan, fake = _plan(monkeypatch, 12)
    assert len(fake.prompts) == 1, "no retry when the model got it right"
    assert plan.warnings == []
    assert len(plan.nodes) == 12


def test_a_short_plan_is_retried_once_with_the_count_in_the_prompt(monkeypatch):
    """Measured 2026-09-05: llama3.1:8b returned 5 nodes where plan.md asks for 10-40."""

    plan, fake = _plan(monkeypatch, 5, 11)
    assert len(fake.prompts) == 2
    assert "returned 5 nodes" in fake.prompts[1]
    assert "at least 6 nodes" in fake.prompts[1]
    assert len(plan.nodes) == 11, "the retry's plan is the one kept"
    assert plan.warnings == []


def test_a_plan_still_short_after_the_retry_comes_back_with_a_warning(monkeypatch):
    plan, fake = _plan(monkeypatch, 5, 5)
    assert len(fake.prompts) == 2, "one retry, not a loop"
    assert len(plan.nodes) == 5, "returned anyway — a short plan beats no plan"
    assert plan.warnings == ["plan has 5 nodes; plan.md asks for 10\u201340"]


def test_the_retry_never_loses_nodes(monkeypatch):
    """A retry that comes back worse is discarded, not preferred for being second."""

    plan, _fake = _plan(monkeypatch, 5, 2)
    assert len(plan.nodes) == 5
    assert plan.warnings == ["plan has 5 nodes; plan.md asks for 10\u201340"]


def test_the_floor_is_configurable(monkeypatch):
    from learning_tutor.tutor import generate as generate_mod

    monkeypatch.setenv("LT_PLAN_MIN_NODES", "3")
    plan, fake = _plan(monkeypatch, 5)
    assert len(fake.prompts) == 1, "5 nodes clears a floor of 3"
    assert plan.warnings == []

    monkeypatch.delenv("LT_PLAN_MIN_NODES")
    assert generate_mod.plan_min_nodes() == 6
    monkeypatch.setenv("LT_PLAN_MIN_NODES", "many")
    with pytest.raises(ValueError, match="LT_PLAN_MIN_NODES"):
        generate_mod.plan_min_nodes()


def test_warnings_never_reach_learner_svc_as_graph_data(monkeypatch):
    """`warnings` is ours. A model that emits the key must not get it into the import."""

    graph = PlanGraph.model_validate(
        {"nodes": [{"title": "A"}], "edges": [], "warnings": ["ignore me"]}
    )
    payload, _dropped = graph.payload()
    assert "warnings" not in payload
    assert payload["nodes"] == [{"title": "A", "aliases": []}]
    assert "warnings" not in graph.model_dump()


# -------------------------------------------------------------------- interrupts


def test_the_interrupt_prompt_carries_the_step_and_forbids_the_checkpoint_answer(monkeypatch):
    """The interrupt answers the step already on screen — so that step is the context."""

    from learning_tutor.tutor import generate as generate_mod

    seen: dict[str, str] = {}

    def fake(prompt, schema, provider_cfg=None, **kwargs):
        seen["user"] = prompt
        seen["system"] = kwargs.get("system_prompt") or ""
        return schema(answer_markdown="Because the definition says so."), None

    monkeypatch.setattr(generate_mod, "generate_structured", fake)
    answer, _meta = generate_mod.generate_interrupt_answer(
        load_pack(),
        goal={"goal_id": "g", "title": "Differential forms", "domain": "math-cs"},
        extras=None,
        node_title="Covectors",
        strategy="example-first",
        step_markdown="Height above the floor eats an arrow and returns a number.",
        question="wait, why does it have to be a number?",
        checkpoint_stem="Which of these is a covector?",
    )

    assert answer.answer_markdown
    user = seen["user"]
    assert "Height above the floor eats an arrow" in user, "the served step is the context"
    assert "wait, why does it have to be a number?" in user
    assert "example-first" in user, "the strategy is stated, never re-chosen"
    assert "Covectors" in user
    assert "Which of these is a covector?" in user
    assert "do not" in user.lower()
    assert "Interrupts mid-step" in user, "teach-step.md is the phase file it composes from"
    # the checkpoint may not be answered, hinted at, or narrowed down
    assert "may NOT state, hint at, or narrow down" in user
    assert "Do not restart" in user
    # philosophy and domain pack are in the system half, corpus never is
    assert seen["system"] and "<<<SOURCE" not in seen["system"]
