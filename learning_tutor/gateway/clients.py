"""HTTP clients for the two services the gateway does not contain: learner-svc, render-svc.

``llm`` and ``corpus`` are *mounted*, not called over the network (CONTRACTS.md: "gateway
+ llm + tutor + corpus in one process, each a FastAPI router that can be lifted out
later"), so they have no client here.

There is no second, direct path into ``events.db``. Every read and every write the gateway
makes about the learner goes through :class:`LearnerClient` — receipts and the passport
included, since ``learner-svc`` grew ``GET /v1/events``, ``/v1/disputes``,
``/v1/misconceptions`` and ``/v1/passport``.

Both clients report "down" and "unconfigured" as different things, because they are: a
missing ``LT_RENDER_URL`` is a deployment choice the tutor degrades around, and a refused
connection to learner-svc is a failure that must stop the session rather than quietly
losing evidence.

Tests inject an ``httpx`` transport (``httpx.ASGITransport(app=learner_svc.create_app())``)
so the whole flow runs in-process with no socket.
"""

from __future__ import annotations

import os
from typing import Any

import httpx

from .errors import GatewayError, UpstreamDown

DEFAULT_LEARNER_URL = "http://localhost:5034"
DEFAULT_TIMEOUT = 60.0


def _env(name: str) -> str | None:
    value = os.environ.get(name)
    return value.strip() if value and value.strip() else None


class LearnerClient:
    """learner-svc over HTTP. Every gateway write to durable state goes through here."""

    service = "learner"

    def __init__(
        self,
        base_url: str | None = None,
        *,
        transport: httpx.BaseTransport | None = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self.base_url = (base_url or _env("LT_LEARNER_URL") or DEFAULT_LEARNER_URL).rstrip("/")
        self._client = httpx.Client(
            base_url=self.base_url, transport=transport, timeout=timeout
        )

    def close(self) -> None:
        self._client.close()

    # ------------------------------------------------------------------ verbs
    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        return self._call("GET", path, params=params)

    def post(
        self,
        path: str,
        body: dict[str, Any] | None = None,
        *,
        idempotency_key: str | None = None,
    ) -> Any:
        headers = {"Idempotency-Key": idempotency_key} if idempotency_key else None
        return self._call("POST", path, json=body or {}, headers=headers)

    def patch(
        self,
        path: str,
        body: dict[str, Any] | None = None,
        *,
        idempotency_key: str | None = None,
    ) -> Any:
        headers = {"Idempotency-Key": idempotency_key} if idempotency_key else None
        return self._call("PATCH", path, json=body or {}, headers=headers)

    def response(self, path: str, params: dict[str, Any] | None = None) -> httpx.Response:
        """The raw response — for text exports whose headers the caller passes on."""

        response = self._send("GET", path, params=params)
        self._raise_for_status(response)
        return response

    def text(self, path: str, params: dict[str, Any] | None = None) -> str:
        response = self._send("GET", path, params=params)
        self._raise_for_status(response)
        return response.text

    def content(self, path: str, params: dict[str, Any] | None = None) -> bytes:
        """The raw body — for ``GET /v1/passport``, which is a zip, not JSON."""

        response = self._send("GET", path, params=params)
        self._raise_for_status(response)
        return response.content

    # --------------------------------------------------------------- plumbing
    def _send(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        try:
            return self._client.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise UpstreamDown(
                self.service, f"{exc.__class__.__name__} calling {self.base_url}{path}: {exc}"
            ) from exc

    def _call(self, method: str, path: str, **kwargs: Any) -> Any:
        response = self._send(method, path, **kwargs)
        self._raise_for_status(response)
        if not response.content:
            return {}
        try:
            return response.json()
        except ValueError:
            return {"text": response.text}

    def _raise_for_status(self, response: httpx.Response) -> None:
        if response.status_code < 400:
            return
        try:
            message = response.json().get("error") or response.text
        except ValueError:
            message = response.text
        # 400/404/409 are the learner's own judgements and are passed through verbatim;
        # anything else is the service failing, which is a 502 from where the caller sits.
        if response.status_code in (400, 404, 409, 422):
            raise GatewayError(
                str(message),
                code={400: "bad_request", 404: "not_found", 409: "conflict", 422: "bad_request"}[
                    response.status_code
                ],
                status=response.status_code,
            )
        raise UpstreamDown(self.service, f"HTTP {response.status_code}: {message}")

    # ------------------------------------------------------------------ probe
    def health(self) -> dict[str, Any]:
        try:
            return {"status": "ok", "detail": self.get("/healthz")}
        except GatewayError as exc:
            return {"status": "down", "detail": exc.message}


class RenderClient:
    """render-svc. Optional by design — the tutor teaches without it, just unrendered."""

    service = "render"

    def __init__(
        self,
        base_url: str | None = None,
        *,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 30.0,
    ) -> None:
        resolved = base_url if base_url is not None else _env("LT_RENDER_URL")
        self.base_url = resolved.rstrip("/") if resolved else None
        self.configured = bool(self.base_url) or transport is not None
        self._timeout = timeout
        self._transport = transport
        self._client: httpx.Client | None = None

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    def _http(self) -> httpx.Client:
        if not self.configured:
            raise UpstreamDown(
                self.service,
                "LT_RENDER_URL is not set, so diagrams are neither checked nor rendered",
                code="render_unconfigured",
            )
        if self._client is None:
            self._client = httpx.Client(
                base_url=self.base_url or "http://render",
                transport=self._transport,
                timeout=self._timeout,
            )
        return self._client

    def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        try:
            response = self._http().post(path, json=body)
        except httpx.HTTPError as exc:
            raise UpstreamDown(self.service, f"{exc.__class__.__name__}: {exc}") from exc
        if response.status_code >= 400:
            raise UpstreamDown(self.service, f"HTTP {response.status_code}: {response.text}")
        return response.json()

    def check(self, mermaid: str) -> dict[str, Any]:
        return self._post("/check", {"mermaid": mermaid})

    def render(self, mermaid: str, *, theme: str = "default") -> dict[str, Any]:
        return self._post("/render", {"mermaid": mermaid, "theme": theme, "format": "svg"})

    def latex_check(self, latex: str, *, html: bool = False) -> dict[str, Any]:
        return self._post("/latex/check", {"latex": latex, "html": html})

    def health(self) -> dict[str, Any]:
        if not self.configured:
            return {"status": "unconfigured", "detail": "LT_RENDER_URL is not set"}
        try:
            response = self._http().get("/healthz")
            if response.status_code >= 400:
                return {"status": "down", "detail": f"HTTP {response.status_code}"}
            return {"status": "ok", "detail": response.json()}
        except (httpx.HTTPError, GatewayError) as exc:
            return {"status": "down", "detail": str(exc)}
