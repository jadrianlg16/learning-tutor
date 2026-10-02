"""`gateway` — the Stage 2 HTTP surface on :5033 (Mode A).

``create_app(settings)`` builds it; ``learn-gateway`` runs it. See :mod:`.app`.
"""

from __future__ import annotations

from .app import DEFAULT_PORT, create_app
from .errors import GatewayError, UpstreamDown
from .state import GatewayState, Question

__all__ = [
    "DEFAULT_PORT",
    "GatewayError",
    "GatewayState",
    "Question",
    "UpstreamDown",
    "create_app",
]
