"""One error envelope for the whole gateway.

CONTRACTS.md *Gateway response shapes*: "errors ``{"error": str, "code": str}`` with
400/404/409/502". Every failure in this package raises :class:`GatewayError`; ``app.py``
installs the one handler that turns it into that envelope. Nothing else formats an error.
"""

from __future__ import annotations


class GatewayError(Exception):
    """A failure with a status and a stable machine-readable ``code``."""

    def __init__(self, message: str, *, code: str = "error", status: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.status = status

    def payload(self) -> dict[str, str]:
        return {"error": self.message, "code": self.code}


class UpstreamDown(GatewayError):
    """A dependency is unreachable or not configured. Always 502."""

    def __init__(self, service: str, detail: str, *, code: str | None = None) -> None:
        super().__init__(
            f"{service} is unavailable: {detail}",
            code=code or f"{service}_down",
            status=502,
        )
        self.service = service


def not_found(what: str) -> GatewayError:
    return GatewayError(what, code="not_found", status=404)


def conflict(what: str, *, code: str = "conflict") -> GatewayError:
    return GatewayError(what, code=code, status=409)


def bad_request(what: str, *, code: str = "bad_request") -> GatewayError:
    return GatewayError(what, code=code, status=400)
