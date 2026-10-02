"""config.get_settings() env parsing and the .env gap-filler."""

from __future__ import annotations

from telegram.config import get_settings, load_dotenv_if_present


def test_get_settings_defaults_match_idea_md():
    settings = get_settings()
    assert settings.budget == 3
    assert settings.window_start.strftime("%H:%M") == "09:00"
    assert settings.window_end.strftime("%H:%M") == "21:00"
    assert settings.min_gap_min == 90
    assert settings.backoff_after_unanswered == 3
    assert settings.backoff_budget == 1
    assert settings.pause_after_days == 7
    assert settings.kill_rate == 0.30
    assert settings.kill_window_days == 14


def test_get_settings_reads_overrides(monkeypatch):
    monkeypatch.setenv("LT_TG_BUDGET", "5")
    monkeypatch.setenv("LT_TG_WINDOW", "07:30-22:15")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "abc")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")

    settings = get_settings()

    assert settings.budget == 5
    assert settings.window_start.strftime("%H:%M") == "07:30"
    assert settings.window_end.strftime("%H:%M") == "22:15"
    assert settings.bot_token == "abc"
    assert settings.chat_id == "42"


def test_load_dotenv_if_present_fills_gaps_without_overriding(tmp_path, monkeypatch):
    # setenv first so monkeypatch records each variable's original state; teardown then
    # removes whatever the .env loader writes, and nothing leaks into later tests
    for key in ("TELEGRAM_BOT_TOKEN", "LT_TG_BUDGET"):
        monkeypatch.setenv(key, "placeholder")
        monkeypatch.delenv(key)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "already-set")

    env_file = tmp_path / ".env"
    env_file.write_text(
        "# comment\n\nTELEGRAM_BOT_TOKEN=from-dotenv\nTELEGRAM_CHAT_ID=should-not-win\n"
        'LT_TG_BUDGET="4"\n',
        encoding="utf-8",
    )

    load_dotenv_if_present(env_file)

    import os

    assert os.environ["TELEGRAM_BOT_TOKEN"] == "from-dotenv"
    assert os.environ["TELEGRAM_CHAT_ID"] == "already-set"  # real env wins
    assert os.environ["LT_TG_BUDGET"] == "4"  # quotes stripped


def test_load_dotenv_if_present_missing_file_is_a_silent_no_op(tmp_path):
    load_dotenv_if_present(tmp_path / "does-not-exist.env")  # must not raise
