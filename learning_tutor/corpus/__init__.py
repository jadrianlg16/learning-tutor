"""The corpus module: the material the tutor teaches from and cites against.

Formerly "verify-svc". Ingest · search · cite, over SQLite FTS5 plus embeddings stored in the
same database — no vector service, per IDEA.md *RAG, plainly*.

Two rules shape every file here:

* **Corpus text is data, never instructions** (`sanitize`). Nothing retrieved reaches a model
  except through ``sanitize.render_for_context``.
* **Citing a slide proves alignment, not truth** (`roles`, `cite`). Every citation is stamped
  with what it actually establishes, and alignment/authority conflicts are shown as two
  labelled readings rather than resolved.

``router`` is imported only where FastAPI is installed; everything else here is importable
with the standard library plus whatever the ingested format needs.
"""

from __future__ import annotations

from .roles import ALIGNMENT, AUTHORITY, LEARNER, ROLES
from .store import CorpusError, CorpusStore, open_store

__all__ = [
    "ALIGNMENT",
    "AUTHORITY",
    "LEARNER",
    "ROLES",
    "CorpusError",
    "CorpusStore",
    "open_store",
]
