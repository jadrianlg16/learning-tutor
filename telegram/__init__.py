"""Stage 1 Telegram micro-review job (optional reminders).

Any scheduler (cron, a systemd timer, Windows Task Scheduler) runs :mod:`telegram.job`
(``tick`` and ``poll``) against this package. Every
number here is either read from the ``learner`` CLI or from ``telegram/state.json`` under
``LT_DATA_DIR`` — nothing about the learner is invented here, and nothing but a retrieval
question is ever pushed unsolicited. See ``docs/modules/telegram.md`` for the full picture.
"""

from __future__ import annotations
