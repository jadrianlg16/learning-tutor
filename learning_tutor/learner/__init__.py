"""Stage 0 learner core: the event store, the graph, items, evidence rules and the views.

``api`` is the surface every transport uses (CLI now; HTTP and MCP at Stage 1).
"""

from .store import LearnerError, Store, open_store

__all__ = ["LearnerError", "Store", "open_store"]
