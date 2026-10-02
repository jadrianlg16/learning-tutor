"""The scripted-learner harness: the automatable gates over a truth-table learner.

Every run gets its own data dir through the conftest ``LT_DATA_DIR``; nothing touches
``./data``.
"""

from __future__ import annotations

import json
import sys
from datetime import timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import sim_learner  # noqa: E402


def _run(data_dir: Path, **over) -> dict:
    config = sim_learner.Config(data_dir=str(data_dir), **over)
    return sim_learner.run(config)


def test_the_forgetting_learner_shows_false_mastery(data_dir):
    report = _run(data_dir, forgets=("N2",), never_knows=("N4",))
    fm = report["false_mastery"]
    assert fm["nodes_with_holdout_checks"] > 0
    assert fm["nodes_known_then_failed"] > 0
    assert fm["rate_percent"] > 0
    assert report["holdout_checks"] > 0
    # the forgotten node was `known` when the holdout was served: that is the definition
    assert report["node_states"]["N2"] == "fragile"
    assert report["node_states"]["N4"] == "unknown"
    # the probe gets shorter once the model has evidence
    probes = report["probe_questions"]
    assert probes["session_2"] < probes["session_1"]
    assert probes["shorter_on_session_2"] is True
    # the rejected item is counted, and the rate has its denominator
    assert report["item_rejection"]["rejected"] == 1
    assert report["item_rejection"]["validations"] > 1


def test_the_perfect_learner_shows_no_false_mastery(data_dir):
    report = _run(data_dir)
    fm = report["false_mastery"]
    assert fm["nodes_with_holdout_checks"] == 4
    assert fm["nodes_known_then_failed"] == 0
    assert fm["rate_percent"] == 0
    assert report["holdout_checks"] == 4
    assert all(state == "known" for state in report["node_states"].values())
    assert report["probe_questions"]["session_2"] == 0
    assert report["holdout_success_7d"]["passes"] == report["holdout_success_7d"]["checks"]


def test_rates_are_null_not_zero_when_nothing_was_measured(data_dir):
    report = _run(data_dir, setup_only=True)
    assert report["false_mastery"]["rate_percent"] is None
    assert report["item_rejection"]["rate_percent"] is None
    assert report["holdout_success_7d"]["rate_percent"] is None
    assert report["holdout_checks"] == 0
    assert report["probe_questions"]["session_1"] is None
    assert report["probe_questions"]["shorter_on_session_2"] is None


def test_a_rule_naming_an_unknown_node_is_an_error(data_dir):
    with pytest.raises(sim_learner.LearnerError):
        _run(data_dir, forgets=("N9",))


def test_the_cli_prints_json_and_pins_the_holdout_delay(data_dir, capsys):
    code = sim_learner.main(["--data-dir", str(data_dir), "--forgets", "N1", "--nodes", "2"])
    assert code == 0
    report = json.loads(capsys.readouterr().out)
    assert report["settings"]["holdout_delay_days"] == 0
    assert report["learner"] == "forgets N1"
    assert report["false_mastery"]["nodes_known_then_failed"] == 1


def test_a_slow_run_still_finds_its_holdouts(data_dir, monkeypatch):
    """Holdouts are stamped with the real clock; a harness clock that started seconds earlier
    (a slow machine, a loaded CI runner) must still see them as due."""

    original = sim_learner.Clock.__init__

    def started_five_seconds_ago(self, gap_days):
        original(self, gap_days)
        self.now -= timedelta(seconds=5)
        self.session2 -= timedelta(seconds=5)
        self.session1 -= timedelta(seconds=5)
        self.current = self.session1

    monkeypatch.setattr(sim_learner.Clock, "__init__", started_five_seconds_ago)
    report = _run(data_dir, forgets=("N2",), never_knows=("N4",))
    assert report["holdout_checks"] > 0
    assert report["false_mastery"]["nodes_known_then_failed"] > 0
