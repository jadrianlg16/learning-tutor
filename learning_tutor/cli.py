"""The ``learner`` CLI — the tool the teach skill shells out to.

JSON in, JSON out. Output is JSON whenever stdout is not a TTY (so every caller that pipes
gets JSON) and whenever ``--json`` is given anywhere on the command line. Failures print
``{"error": ...}`` on stderr and exit non-zero.

The model never edits numbers: it calls one of these commands, and code recomputes state,
``state.json`` and ``learner.md``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import typer
from typer._click import exceptions as click_exceptions

from .config import get_settings
from .learner import api
from .learner.models import DEFAULT_GRADER_VERSION, DEFAULT_PROMPT_VERSION
from .learner.store import LearnerError, Store, open_store

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Learner model: events, graph, items, evidence, scheduling, views.",
)
goal_app = typer.Typer(no_args_is_help=True, help="Goals.")
graph_app = typer.Typer(no_args_is_help=True, help="The concept graph.")
item_app = typer.Typer(no_args_is_help=True, help="The item bank and its lifecycle.")
session_app = typer.Typer(no_args_is_help=True, help="Sessions.")
record_app = typer.Typer(no_args_is_help=True, help="Record evidence.")
misconception_app = typer.Typer(no_args_is_help=True, help="Misconceptions.")
dispute_app = typer.Typer(no_args_is_help=True, help="Typed disputes.")
study_app = typer.Typer(no_args_is_help=True, help="Import study material from markdown.")
bank_app = typer.Typer(no_args_is_help=True, help="The imported question bank.")
practice_app = typer.Typer(no_args_is_help=True, help="Practice questions, graded by key.")
cards_app = typer.Typer(no_args_is_help=True, help="Flashcards (flips are self-report).")
table_app = typer.Typer(no_args_is_help=True, help="Comparison and definition tables.")
mock_app = typer.Typer(
    no_args_is_help=True, help="Sealed mock exams: no feedback until the whole mock is submitted."
)
app.add_typer(goal_app, name="goal")
app.add_typer(graph_app, name="graph")
app.add_typer(item_app, name="item")
app.add_typer(session_app, name="session")
app.add_typer(record_app, name="record")
app.add_typer(misconception_app, name="misconception")
app.add_typer(dispute_app, name="dispute")
app.add_typer(study_app, name="study")
app.add_typer(bank_app, name="bank")
app.add_typer(practice_app, name="practice")
app.add_typer(cards_app, name="cards")
app.add_typer(table_app, name="table")
app.add_typer(mock_app, name="mock")

_STATE: dict[str, Any] = {"json": None, "data_dir": None}


# --------------------------------------------------------------------------- plumbing
def _json_mode() -> bool:
    if _STATE["json"] is not None:
        return bool(_STATE["json"])
    return not sys.stdout.isatty()


def _settings():
    return get_settings(_STATE["data_dir"])


def _store() -> Store:
    return open_store(_settings())


def _plain(data: Any, indent: str = "") -> str:
    if isinstance(data, dict):
        lines = []
        for key, value in data.items():
            if isinstance(value, (dict, list)) and value:
                lines.append(f"{indent}{key}:")
                lines.append(_plain(value, indent + "  "))
            else:
                lines.append(f"{indent}{key}: {value}")
        return "\n".join(lines)
    if isinstance(data, list):
        return "\n".join(
            _plain(v, indent) if isinstance(v, (dict, list)) else f"{indent}- {v}"
            for v in data
        )
    return f"{indent}{data}"


def emit(data: Any, *, text: str | None = None) -> None:
    if _json_mode():
        typer.echo(json.dumps(data, indent=2, default=str, ensure_ascii=False))
    else:
        typer.echo(text if text is not None else _plain(data))


def _read_json(path: str) -> Any:
    file = Path(path)
    if not file.exists():
        raise LearnerError(f"no such file: {path}")
    try:
        return json.loads(file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise LearnerError(f"{path} is not valid JSON: {exc}") from exc


def _read_text(path: str) -> str:
    """A UTF-8 text file (a leading BOM, as Windows editors write, is dropped)."""

    file = Path(path)
    if not file.is_file():
        raise LearnerError(f"no such file: {path}")
    try:
        return file.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError as exc:
        raise LearnerError(f"{path} is not UTF-8 text: {exc}") from exc


def _parse_order(text: str | None) -> list[int] | None:
    """``--order 2,0,1`` as the list the MCP tool and the HTTP body carry. Whether it is a
    permutation of the question's options is the core's check, not this one."""

    if text is None or not text.strip():
        return None
    parts = [p for p in text.replace(",", " ").split() if p]
    try:
        return [int(p) for p in parts]
    except ValueError:
        raise LearnerError(
            f"--order must be option indexes separated by commas (e.g. 2,0,1), got {text!r}"
        ) from None


# --------------------------------------------------------------------------- commands
@app.callback()
def root(
    data_dir: str = typer.Option(
        None, "--data-dir", help="Override LT_DATA_DIR (accepted before or after the command)."
    ),
    json_out: bool = typer.Option(
        False, "--json", help="Force JSON output (the default when stdout is not a TTY)."
    ),
) -> None:
    if data_dir:
        _STATE["data_dir"] = data_dir
    if json_out:
        _STATE["json"] = True


@app.command()
def init(data_dir: str = typer.Option(None, "--data-dir")) -> None:
    """Create the data directory, the SQLite store and notes.md."""

    if data_dir:
        _STATE["data_dir"] = data_dir
    emit(api.init(_settings()))


@goal_app.command("add")
def goal_add(
    goal_id: str = typer.Option(..., "--id"),
    title: str = typer.Option(..., "--title"),
    depth: str = typer.Option("explain", "--depth"),
    deadline: str = typer.Option(None, "--deadline"),
    minutes_per_session: int = typer.Option(None, "--minutes-per-session"),
    purpose: str = typer.Option(None, "--purpose"),
    assessment: str = typer.Option(
        None, "--assessment", help="How success is assessed (exam, project, conversation)."
    ),
    source_priority: str = typer.Option(
        None,
        "--source-priority",
        help="alignment|authority: which wins when the syllabus and the reference disagree.",
    ),
    idempotency_key: str = typer.Option(None, "--idempotency-key"),
) -> None:
    """Add a goal: concept + depth + purpose (+ deadline and session length)."""

    with _store() as store:
        emit(
            api.goal_add(
                store,
                goal_id=goal_id,
                title=title,
                depth=depth,
                deadline=deadline,
                minutes_per_session=minutes_per_session,
                purpose=purpose,
                assessment=assessment,
                source_priority=source_priority,
                idempotency_key=idempotency_key,
            )
        )


@goal_app.command("update")
def goal_update(
    goal: str = typer.Option(None, "--goal"),
    deadline: str = typer.Option(None, "--deadline", help='YYYY-MM-DD, or "" to clear it.'),
    minutes_per_session: int = typer.Option(None, "--minutes-per-session"),
    sessions_per_week: int = typer.Option(None, "--sessions-per-week"),
    title: str = typer.Option(None, "--title"),
    purpose: str = typer.Option(None, "--purpose"),
    assessment: str = typer.Option(None, "--assessment"),
    depth: str = typer.Option(None, "--depth", help="recognize|explain|apply|analyze"),
    source_priority: str = typer.Option(None, "--source-priority", help="alignment|authority"),
    idempotency_key: str = typer.Option(None, "--idempotency-key"),
) -> None:
    """Change a goal's date, cadence or wording. Omitted flags are left as they are."""

    with _store() as store:
        emit(
            api.goal_update(
                store,
                api.resolve_goal(store, goal),
                idempotency_key=idempotency_key,
                deadline=deadline,
                minutes_per_session=minutes_per_session,
                sessions_per_week=sessions_per_week,
                title=title,
                purpose=purpose,
                assessment=assessment,
                depth=depth,
                source_priority=source_priority,
            )
        )


@goal_app.command("blueprint")
def goal_blueprint(
    goal: str = typer.Option(None, "--goal"),
    file: str = typer.Option(
        None,
        "--file",
        help='Set (replace) from JSON: {"exam", "source", "areas": [{"code", "title", '
        '"subareas": [{"ref", "title", "items"}]}]}. Without it, show the stored one.',
    ),
    author: str = typer.Option(
        None, "--author", help="Who set it (with --file). Default: the file's author, else cli."
    ),
    idempotency_key: str = typer.Option(None, "--idempotency-key"),
) -> None:
    """The exam's item count per concept. Every ref must name a concept of the goal."""

    spec = None
    if file is not None:
        data = _read_json(file)
        if not isinstance(data, dict):
            raise LearnerError(f"{file} must hold one blueprint object")
        # the fields PUT /v1/goals/{g}/blueprint and the MCP tool pass on, and no others
        spec = {key: data.get(key) for key in ("exam", "source", "areas")}
        author = author or data.get("author") or "cli"
    with _store() as store:
        goal_id = api.resolve_goal(store, goal)
        if spec is None:
            emit(api.goal_blueprint(store, goal_id))
            return
        emit(
            api.goal_blueprint_set(
                store, goal_id, spec, author=author, idempotency_key=idempotency_key
            )
        )


@graph_app.command("import")
def graph_import(
    goal: str = typer.Option(..., "--goal"),
    file: str = typer.Option(..., "--file"),
    idempotency_key: str = typer.Option(None, "--idempotency-key"),
) -> None:
    """Import {nodes:[{id?,title,aliases[]}], edges:[{from,to,type,provenance}]}."""

    with _store() as store:
        emit(
            api.graph_import(
                store, goal, _read_json(file), idempotency_key=idempotency_key
            )
        )


@graph_app.command("show")
def graph_show(
    goal: str = typer.Option(None, "--goal"),
    fmt: str = typer.Option("json", "--format"),
) -> None:
    """Show the goal's graph as json, or as mermaid coloured by node state."""

    if fmt not in ("json", "mermaid"):
        raise LearnerError("format must be json or mermaid")
    with _store() as store:
        goal = api.resolve_goal(store, goal)
        result = api.graph_show(store, goal, fmt)
    if fmt == "mermaid":
        emit({"goal_id": goal, "format": "mermaid", "mermaid": result}, text=result)
    else:
        emit(result)


@graph_app.command("revise")
def graph_revise(
    goal: str = typer.Option(..., "--goal"),
    ops: str = typer.Option(..., "--ops"),
    idempotency_key: str = typer.Option(None, "--idempotency-key"),
) -> None:
    """Apply add/remove/split/merge ops; evidence migrates with the nodes."""

    with _store() as store:
        emit(api.graph_revise(store, goal, _read_json(ops), idempotency_key=idempotency_key))


@item_app.command("add")
def item_add(
    node: str = typer.Option(..., "--node"),
    file: str = typer.Option(..., "--file"),
    author: str = typer.Option("model", "--author"),
    item: str = typer.Option(None, "--item", help="Add a new version of an existing item."),
    idempotency_key: str = typer.Option(None, "--idempotency-key"),
) -> None:
    """Add an item (status TEACHING_ONLY). Editing a stem creates a new version."""

    with _store() as store:
        emit(
            api.item_add(
                store,
                node,
                _read_json(file),
                author=author,
                item=item,
                idempotency_key=idempotency_key,
            )
        )


@item_app.command("validate")
def item_validate(
    item: str = typer.Option(..., "--item"),
    by: str = typer.Option(..., "--by"),
    result: str = typer.Option(..., "--result"),
    notes: str = typer.Option(None, "--notes"),
    evaluation_method: str = typer.Option(
        None,
        "--evaluation-method",
        help="host_llm|blind_solver|rubric|human. A host_llm pass never promotes the item.",
    ),
    idempotency_key: str = typer.Option(None, "--idempotency-key"),
) -> None:
    """Record an independent check. A pass promotes the item to PRACTICE_EVIDENCE."""

    with _store() as store:
        emit(
            api.item_validate(
                store,
                item,
                by=by,
                result=result,
                notes=notes,
                evaluation_method=evaluation_method,
                idempotency_key=idempotency_key,
            )
        )


@item_app.command("promote")
def item_promote(
    item: str = typer.Option(..., "--item"),
    idempotency_key: str = typer.Option(None, "--idempotency-key"),
) -> None:
    """Promote to MASTERY_ELIGIBLE (rule-checked) and assign holdout membership."""

    with _store() as store:
        emit(api.item_promote(store, item, idempotency_key=idempotency_key))


@item_app.command("blind-check")
def item_blind_check(
    item: str = typer.Option(..., "--item"),
    answer: str = typer.Option(
        None,
        "--answer",
        help="The solver's pick: option key or text. Omit it (with --ambiguous) when the "
        "solver found no single best option.",
    ),
    by: str = typer.Option(..., "--by", help="The solver; must not be the question's author."),
    ambiguous: bool = typer.Option(False, "--ambiguous"),
    notes: str = typer.Option(None, "--notes"),
    idempotency_key: str = typer.Option(None, "--idempotency-key"),
) -> None:
    """Record a blind solve. The key is compared here and never printed."""

    with _store() as store:
        emit(
            api.item_blind_check(
                store,
                item,
                answer=answer,
                by=by,
                ambiguous=ambiguous,
                notes=notes,
                idempotency_key=idempotency_key,
            )
        )


@session_app.command("start")
def session_start(
    goal: str = typer.Option(..., "--goal"),
    channel: str = typer.Option("claude-code", "--channel"),
    idempotency_key: str = typer.Option(None, "--idempotency-key"),
) -> None:
    """Start a session."""

    with _store() as store:
        emit(
            api.session_start(
                store, goal_id=goal, channel=channel, idempotency_key=idempotency_key
            )
        )


@session_app.command("end")
def session_end(
    session: str = typer.Option(..., "--session"),
    summary: str = typer.Option(None, "--summary"),
    idempotency_key: str = typer.Option(None, "--idempotency-key"),
) -> None:
    """End a session."""

    with _store() as store:
        emit(api.session_end(store, session, summary, idempotency_key=idempotency_key))


@app.command("next")
def next_cmd(
    goal: str = typer.Option(None, "--goal"),
    session: str = typer.Option(None, "--session"),
    n: int = typer.Option(1, "--n"),
    mode: str = typer.Option("auto", "--mode", help="probe|review|teach (default: auto)"),
) -> None:
    """Pick what to ask next. Never returns a holdout item."""

    with _store() as store:
        emit(api.next_(store, api.resolve_goal(store, goal), mode=mode, n=n, session_id=session))


@record_app.command("answer")
def record_answer(
    session: str = typer.Option(None, "--session"),
    item: str = typer.Option(..., "--item"),
    response: str = typer.Option(None, "--response"),
    correct: int = typer.Option(..., "--correct"),
    confidence: int = typer.Option(None, "--confidence"),
    idk: bool = typer.Option(False, "--idk"),
    assistance: int = typer.Option(0, "--assistance"),
    context: str = typer.Option("in-session", "--context"),
    channel: str = typer.Option(None, "--channel"),
    prompt_version: str = typer.Option(
        DEFAULT_PROMPT_VERSION, "--prompt-version", help="Prompt pack version that asked."
    ),
    grader_version: str = typer.Option(
        DEFAULT_GRADER_VERSION, "--grader-version", help="Version of whatever judged it."
    ),
    evaluation_method: str = typer.Option(
        api.DEFAULT_EVALUATION_METHOD,
        "--evaluation-method",
        help=(
            "host_llm|blind_solver|rubric|human. host_llm is the tutoring model grading "
            "its own learner: recorded, but never counted toward mastery."
        ),
    ),
    idempotency_key: str = typer.Option(None, "--idempotency-key"),
) -> None:
    """Record one answer. Assistance 5-6, and a host_llm grade, never count toward mastery."""

    with _store() as store:
        emit(
            api.record_answer(
                store,
                session_id=session,
                item_id=item,
                response=response,
                correct=bool(correct),
                confidence=confidence,
                idk=idk,
                assistance_level=assistance,
                context=context,
                channel=channel,
                prompt_version=prompt_version,
                grader_version=grader_version,
                evaluation_method=evaluation_method,
                idempotency_key=idempotency_key,
            )
        )


@record_app.command("teach-back")
def record_teach_back(
    session: str = typer.Option(None, "--session"),
    node: str = typer.Option(..., "--node"),
    score: int = typer.Option(..., "--score"),
    rubric_version: str = typer.Option(..., "--rubric-version"),
    assistance: int = typer.Option(0, "--assistance"),
    notes: str = typer.Option(None, "--notes"),
    context: str = typer.Option("in-session", "--context"),
    prompt_version: str = typer.Option(DEFAULT_PROMPT_VERSION, "--prompt-version"),
    grader_version: str = typer.Option(
        None, "--grader-version", help="Defaults to --rubric-version: the rubric is the grader."
    ),
    idempotency_key: str = typer.Option(None, "--idempotency-key"),
) -> None:
    """Record a teach-back, scored 0-3 against a versioned rubric."""

    with _store() as store:
        emit(
            api.record_teach_back(
                store,
                session_id=session,
                node=node,
                score=score,
                rubric_version=rubric_version,
                assistance_level=assistance,
                notes=notes,
                context=context,
                prompt_version=prompt_version,
                grader_version=grader_version,
                idempotency_key=idempotency_key,
            )
        )


@misconception_app.command("suspect")
def misconception_suspect(
    node: str = typer.Option(..., "--node"),
    claim: str = typer.Option(..., "--claim"),
    session: str = typer.Option(None, "--session"),
    prompt_version: str = typer.Option(DEFAULT_PROMPT_VERSION, "--prompt-version"),
    grader_version: str = typer.Option(DEFAULT_GRADER_VERSION, "--grader-version"),
    idempotency_key: str = typer.Option(None, "--idempotency-key"),
) -> None:
    """Open a hypothesis. It is not durable state until all three steps hold."""

    with _store() as store:
        emit(
            api.misconception_suspect(
                store,
                node=node,
                claim=claim,
                session_id=session,
                prompt_version=prompt_version,
                grader_version=grader_version,
                idempotency_key=idempotency_key,
            )
        )


@misconception_app.command("confirm-step")
def misconception_confirm_step(
    node: str = typer.Option(..., "--node"),
    claim: str = typer.Option(..., "--claim"),
    step: str = typer.Option(..., "--step", help="reasoning|prediction|counterexample, in order"),
    outcome: str = typer.Option(..., "--outcome", help="held|dropped"),
    notes: str = typer.Option(None, "--notes"),
    prompt_version: str = typer.Option(DEFAULT_PROMPT_VERSION, "--prompt-version"),
    grader_version: str = typer.Option(DEFAULT_GRADER_VERSION, "--grader-version"),
    idempotency_key: str = typer.Option(None, "--idempotency-key"),
) -> None:
    """Record one confirmation step. Only all three held make it active."""

    with _store() as store:
        emit(
            api.misconception_confirm_step(
                store,
                node=node,
                claim=claim,
                step=step,
                outcome=outcome,
                notes=notes,
                prompt_version=prompt_version,
                grader_version=grader_version,
                idempotency_key=idempotency_key,
            )
        )


@misconception_app.command("resolve")
def misconception_resolve(
    node: str = typer.Option(..., "--node"),
    claim: str = typer.Option(..., "--claim"),
    notes: str = typer.Option(None, "--notes"),
    prompt_version: str = typer.Option(DEFAULT_PROMPT_VERSION, "--prompt-version"),
    grader_version: str = typer.Option(DEFAULT_GRADER_VERSION, "--grader-version"),
    idempotency_key: str = typer.Option(None, "--idempotency-key"),
) -> None:
    """Close a misconception."""

    with _store() as store:
        emit(
            api.misconception_resolve(
                store,
                node=node,
                claim=claim,
                notes=notes,
                prompt_version=prompt_version,
                grader_version=grader_version,
                idempotency_key=idempotency_key,
            )
        )


@dispute_app.command("open")
def dispute_open(
    dispute_type: str = typer.Option(..., "--type"),
    node: str = typer.Option(None, "--node"),
    item: str = typer.Option(None, "--item"),
    note: str = typer.Option(None, "--note"),
    session: str = typer.Option(None, "--session"),
    idempotency_key: str = typer.Option(None, "--idempotency-key"),
) -> None:
    """Open a typed dispute. "I already know this" schedules a check; it never grants mastery."""

    with _store() as store:
        emit(
            api.dispute_open(
                store,
                dispute_type=dispute_type,
                node=node,
                item_id=item,
                note=note,
                session_id=session,
                idempotency_key=idempotency_key,
            )
        )


@dispute_app.command("settle")
def dispute_settle(
    dispute: str = typer.Option(..., "--dispute"),
    outcome: str = typer.Option(..., "--outcome"),
    evidence: str = typer.Option(None, "--evidence"),
    idempotency_key: str = typer.Option(None, "--idempotency-key"),
) -> None:
    """Settle a dispute with evidence. State is still recomputed from events."""

    with _store() as store:
        emit(
            api.dispute_settle(
                store,
                dispute,
                outcome=outcome,
                evidence=evidence,
                idempotency_key=idempotency_key,
            )
        )


@app.command()
def summary(
    goal: str = typer.Option(None, "--goal"),
    fmt: str = typer.Option("md", "--format"),
) -> None:
    """Regenerate learner.md (+ state.json) and print it."""

    if fmt not in ("md", "json"):
        raise LearnerError("format must be md or json")
    with _store() as store:
        result = api.summary(store, api.resolve_goal(store, goal), fmt)
    emit(result, text=result["markdown"] if fmt == "md" else None)


@app.command("holdout-check")
def holdout_check(goal: str = typer.Option(None, "--goal")) -> None:
    """Serve due holdout items — the delayed and transfer metrics."""

    with _store() as store:
        emit(api.holdout_check(store, api.resolve_goal(store, goal) if goal else None))


@app.command()
def metrics(goal: str = typer.Option(None, "--goal")) -> None:
    """7-day holdout success, false mastery, item rejection rate."""

    with _store() as store:
        emit(api.metrics(store, api.resolve_goal(store, goal) if goal else None))


@app.command()
def export(out: str = typer.Option(None, "--out")) -> None:
    """Export the event table to events.jsonl."""

    with _store() as store:
        emit(api.export(store, out))


@app.command()
def log(
    session: str = typer.Option(..., "--session"),
    file: str = typer.Option(..., "--file"),
    idempotency_key: str = typer.Option(None, "--idempotency-key"),
) -> None:
    """Copy a markdown session log into the vault."""

    with _store() as store:
        emit(api.log_session(store, session, file, idempotency_key=idempotency_key))


# --------------------------------------------------------------------------- study tools
@study_app.command("import")
def study_import(
    goal: str = typer.Option(None, "--goal"),
    file: str = typer.Option(..., "--file", help="Markdown notes, questions or a spec."),
    key_file: str = typer.Option(
        None, "--key-file", help="A separate answer key (default: read it from --file)."
    ),
    what: str = typer.Option(
        None, "--what", help="Comma list of questions,cards,tables (default: all)."
    ),
    author: str = typer.Option("import", "--author"),
    node: str = typer.Option(None, "--node", help="Concept for blocks with no tagged heading."),
    no_create_nodes: bool = typer.Option(
        False, "--no-create-nodes", help="Do not add concepts for tagged headings."
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Report what would be imported."),
    source: str = typer.Option(None, "--source", help="Label (default: the file name)."),
    pool: str = typer.Option(
        "practice",
        "--pool",
        help="practice|mock. mock seals the questions for mock exams (questions only): they "
        "are never served by practice, cards or exports until a mock has used them.",
    ),
    idempotency_key: str = typer.Option(None, "--idempotency-key"),
) -> None:
    """Import questions, flashcards and tables from markdown. Questions start TEACHING_ONLY."""

    markdown = _read_text(file)
    key_markdown = _read_text(key_file) if key_file else None
    kinds = [w.strip() for w in what.split(",") if w.strip()] if what else None
    with _store() as store:
        emit(
            api.study_import(
                store,
                api.resolve_goal(store, goal),
                markdown,
                key_markdown=key_markdown,
                what=kinds or None,
                source=source or Path(file).name,
                author=author,
                node=node,
                create_nodes=not no_create_nodes,
                dry_run=dry_run,
                pool=pool,
                idempotency_key=idempotency_key,
            )
        )


@bank_app.command("pending")
def bank_pending(
    goal: str = typer.Option(None, "--goal"),
    limit: int = typer.Option(25, "--limit"),
) -> None:
    """Questions awaiting a blind check: stem and options, no keys."""

    with _store() as store:
        emit(api.bank_pending(store, api.resolve_goal(store, goal), limit=limit))


@bank_app.command("review")
def bank_review(goal: str = typer.Option(None, "--goal")) -> None:
    """Questions that failed a blind check, WITH keys — for a person to judge."""

    with _store() as store:
        emit(api.bank_review(store, api.resolve_goal(store, goal)))


@practice_app.command("next")
def practice_next(
    goal: str = typer.Option(None, "--goal"),
    n: int = typer.Option(1, "--n"),
    focus: str = typer.Option(
        None,
        "--focus",
        help="An area code of the blueprint (3) or a concept (ref 3.2, alias, id or title). "
        "Omit it for mixed practice.",
    ),
) -> None:
    """Due questions first, then new ones interleaved by the blueprint (daily cap). No keys.

    Options come shuffled: letters follow each question's `order`; pass it back to answer."""

    with _store() as store:
        emit(api.practice_next(store, api.resolve_goal(store, goal), n=n, focus=focus))


@practice_app.command("answer")
def practice_answer(
    item: str = typer.Option(..., "--item"),
    response: str = typer.Option(
        None, "--response", help="Option letter as shown (B) or the option's text."
    ),
    order: str = typer.Option(
        None,
        "--order",
        help="The question's `order` from practice next, e.g. 2,0,1. Without it, today's "
        "shuffle is assumed.",
    ),
    confidence: int = typer.Option(None, "--confidence", help="1-5"),
    idk: bool = typer.Option(False, "--idk"),
    session: str = typer.Option(None, "--session"),
    channel: str = typer.Option(None, "--channel"),
    idempotency_key: str = typer.Option(None, "--idempotency-key"),
) -> None:
    """Grade an answer against the stored key, record it and schedule the question."""

    shown = _parse_order(order)
    with _store() as store:
        emit(
            api.practice_answer(
                store,
                item_id=item,
                response=response,
                order=shown,
                confidence=confidence,
                idk=idk,
                session_id=session,
                channel=channel,
                idempotency_key=idempotency_key,
            )
        )


# --------------------------------------------------------------------------- exam prep
# CONTRACTS.md, *Exam blueprint, mixed practice and sealed mock exams*.
@app.command()
def progress(goal: str = typer.Option(None, "--goal")) -> None:
    """Counts against the blueprint: first tries per area with a 95% range, what is due,
    the last 28 days, the due forecast to the deadline, and past mocks. Not a score."""

    with _store() as store:
        emit(api.progress(store, api.resolve_goal(store, goal)))


@mock_app.command("start")
def mock_start(
    goal: str = typer.Option(None, "--goal"),
    n: int = typer.Option(
        None, "--n", help="Questions to draw (default: every checked sealed one, up to 60)."
    ),
    minutes: int = typer.Option(
        None, "--minutes", help="Time limit (default: LT_MOCK_MINUTES_PER_ITEM per question)."
    ),
    channel: str = typer.Option("claude-code", "--channel"),
    idempotency_key: str = typer.Option(None, "--idempotency-key"),
) -> None:
    """Open a sealed mock: questions and shuffled options, no keys, no feedback until submit."""

    with _store() as store:
        emit(
            api.mock_start(
                store,
                api.resolve_goal(store, goal),
                n=n,
                minutes=minutes,
                channel=channel,
                idempotency_key=idempotency_key,
            )
        )


@mock_app.command("show")
def mock_show(session: str = typer.Option(..., "--session")) -> None:
    """An open mock's questions (no keys), or a submitted mock's graded result."""

    with _store() as store:
        emit(api.mock_show(store, session))


@mock_app.command("submit")
def mock_submit(
    session: str = typer.Option(..., "--session"),
    file: str = typer.Option(
        ...,
        "--file",
        help='JSON: [{"item_id", "response", "order"?, "confidence"?}] (or {"answers": [...]}). '
        "response is a letter of the mock's order or the option's text; a question left out "
        "or with a null response is recorded as I don't know.",
    ),
    idempotency_key: str = typer.Option(None, "--idempotency-key"),
) -> None:
    """Grade every answer at once and close the mock. Only then are keys shown."""

    data = _read_json(file)
    answers = data.get("answers") if isinstance(data, dict) else data
    if not isinstance(answers, list) or not all(isinstance(a, dict) for a in answers):
        raise LearnerError(
            f"{file} must hold a list of answer objects (or {{\"answers\": [...]}})"
        )
    with _store() as store:
        emit(api.mock_submit(store, session, answers, idempotency_key=idempotency_key))


@mock_app.command("list")
def mock_list(goal: str = typer.Option(None, "--goal")) -> None:
    """Sealed questions available per area, the open mock (if any) and past mocks."""

    with _store() as store:
        emit(api.mock_list(store, api.resolve_goal(store, goal)))


@cards_app.command("add")
def cards_add(
    goal: str = typer.Option(None, "--goal"),
    file: str = typer.Option(..., "--file", help='JSON: [{"front", "back", "node"}].'),
    author: str = typer.Option("model", "--author"),
    source: str = typer.Option(None, "--source"),
    idempotency_key: str = typer.Option(None, "--idempotency-key"),
) -> None:
    """Add flashcards written by the harness. Duplicates are skipped."""

    data = _read_json(file)
    cards = data.get("cards") if isinstance(data, dict) else data
    if not isinstance(cards, list):
        raise LearnerError(f"{file} must hold a list of cards (or {{\"cards\": [...]}})")
    with _store() as store:
        emit(
            api.cards_add(
                store,
                api.resolve_goal(store, goal),
                cards,
                author=author,
                source=source,
                idempotency_key=idempotency_key,
            )
        )


@cards_app.command("next")
def cards_next(
    goal: str = typer.Option(None, "--goal"),
    n: int = typer.Option(1, "--n"),
) -> None:
    """Due cards first, then new ones (daily cap). Fronts only."""

    with _store() as store:
        emit(api.cards_next(store, api.resolve_goal(store, goal), n=n))


@cards_app.command("reveal")
def card_reveal(item: str = typer.Option(..., "--item")) -> None:
    """The back of a card — after the learner has tried to recall it."""

    with _store() as store:
        emit(api.card_reveal(store, item))


@cards_app.command("review")
def card_review(
    item: str = typer.Option(..., "--item"),
    rating: str = typer.Option(..., "--rating", help="again|hard|good|easy"),
    session: str = typer.Option(None, "--session"),
    channel: str = typer.Option(None, "--channel"),
    idempotency_key: str = typer.Option(None, "--idempotency-key"),
) -> None:
    """Record the learner's self-rating. Schedules the card; never evidence."""

    with _store() as store:
        emit(
            api.card_review(
                store,
                item,
                rating=rating,
                session_id=session,
                channel=channel,
                idempotency_key=idempotency_key,
            )
        )


@cards_app.command("export")
def cards_export(
    goal: str = typer.Option(None, "--goal"),
    fmt: str = typer.Option("tsv", "--format", help="tsv|csv"),
    include: str = typer.Option("cards", "--include", help="cards|questions|all"),
    out: str = typer.Option(None, "--out", help="Write here; without it the text is printed."),
) -> None:
    """Anki-importable text: front, back, tags. Printed raw unless --out is given."""

    with _store() as store:
        result = api.cards_export(store, api.resolve_goal(store, goal), fmt=fmt, include=include)
    if out is None:
        typer.echo(result["text"], nl=False)
        return
    target = Path(out)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(result["text"], encoding="utf-8", newline="")
    emit({"count": result["count"], "out": str(target.resolve())})


@table_app.command("list")
def table_list(goal: str = typer.Option(None, "--goal")) -> None:
    """The goal's tables, without their rows."""

    with _store() as store:
        emit(api.tables_list(store, api.resolve_goal(store, goal)))


@table_app.command("show")
def table_show(table: str = typer.Option(..., "--table")) -> None:
    """One table with its rows."""

    with _store() as store:
        emit(api.table_get(store, table))


@table_app.command("save")
def table_save(
    goal: str = typer.Option(None, "--goal"),
    file: str = typer.Option(
        ..., "--file", help='JSON: {"title", "columns": [..], "rows": [[..]], "node"?, "source"?}.'
    ),
    author: str = typer.Option(None, "--author", help="Default: the file's author, else model."),
    idempotency_key: str = typer.Option(None, "--idempotency-key"),
) -> None:
    """Save a comparison or definition table. Identical content is stored once."""

    data = _read_json(file)
    if not isinstance(data, dict):
        raise LearnerError(f"{file} must hold one table object")
    with _store() as store:
        emit(
            api.table_save(
                store,
                api.resolve_goal(store, goal),
                title=data.get("title", ""),
                columns=data.get("columns") or [],
                rows=data.get("rows") or [],
                node=data.get("node"),
                source=data.get("source"),
                author=author or data.get("author") or "model",
                idempotency_key=idempotency_key,
            )
        )


@table_app.command("cards")
def table_cards(
    table: str = typer.Option(..., "--table"),
    author: str = typer.Option("table", "--author"),
    idempotency_key: str = typer.Option(None, "--idempotency-key"),
) -> None:
    """Turn a table (filed under a concept) into flashcards."""

    with _store() as store:
        emit(api.table_cards(store, table, author=author, idempotency_key=idempotency_key))


# --------------------------------------------------------------------------- entry point
def _extract_global_flags(argv: list[str]) -> list[str]:
    """Accept --json and --data-dir anywhere on the command line."""

    out: list[str] = []
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--json":
            _STATE["json"] = True
        elif arg == "--no-json":
            _STATE["json"] = False
        elif arg == "--data-dir":
            if i + 1 >= len(argv):
                raise LearnerError("--data-dir needs a value")
            _STATE["data_dir"] = argv[i + 1]
            i += 1
        elif arg.startswith("--data-dir="):
            _STATE["data_dir"] = arg.split("=", 1)[1]
        else:
            out.append(arg)
        i += 1
    return out


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):  # pragma: no cover - non-standard streams
            pass
    try:
        args = _extract_global_flags(args)
        app(args=args, standalone_mode=False)
    except LearnerError as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        raise SystemExit(1) from None
    except NotImplementedError as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        raise SystemExit(1) from None
    except typer.Exit as exc:
        raise SystemExit(exc.exit_code) from None
    except typer.Abort:
        print(json.dumps({"error": "aborted"}), file=sys.stderr)
        raise SystemExit(130) from None
    except click_exceptions.ClickException as exc:
        print(json.dumps({"error": exc.format_message()}), file=sys.stderr)
        raise SystemExit(2) from None
    except Exception as exc:  # pragma: no cover - unexpected
        print(json.dumps({"error": f"{type(exc).__name__}: {exc}"}), file=sys.stderr)
        raise SystemExit(1) from None
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
