from __future__ import annotations

from typing import Any

import httpx

from capelle_platform.observability.logger import get_logger

_logger = get_logger(__name__)


class LangfuseClient:
    """Thin Langfuse ingestion client over httpx. Never raises.

    Posts a batch of envelope events to ``/api/public/ingestion`` with HTTP
    Basic auth (public_key:secret_key). Any failure is logged and reported
    as ``False`` so observability can never break a job.
    """

    def __init__(
        self,
        host: str,
        public_key: str,
        secret_key: str,
        *,
        timeout: float = 5.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        """Construct a LangfuseClient.

        Args:
            host: Langfuse base URL (e.g. ``http://langfuse:3000``).
            public_key: Langfuse public API key (``pk-lf-…``).
            secret_key: Langfuse secret API key (``sk-lf-…``).
            timeout: HTTP timeout in seconds. Defaults to 5.0 so a slow
                Langfuse never delays job processing.
            transport: Optional httpx transport override (used in tests via
                ``httpx.MockTransport``).
        """
        self._url = host.rstrip("/") + "/api/public/ingestion"
        self._auth = (public_key, secret_key)
        self._timeout = timeout
        self._transport = transport

    async def send_batch(self, events: list[dict[str, Any]]) -> bool:
        """Post a batch of ingestion events to Langfuse.

        Wraps the entire call in a broad ``except`` so no Langfuse failure
        (network, auth, server error) can ever propagate to the caller.

        Args:
            events: List of Langfuse envelope events (each is a dict with
                ``id``, ``type``, and ``body``).

        Returns:
            ``True`` on success (including an empty batch), ``False`` on any
            failure.
        """
        if not events:
            return True
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout, transport=self._transport
            ) as client:
                resp = await client.post(
                    self._url, json={"batch": events}, auth=self._auth
                )
                resp.raise_for_status()
            return True
        except Exception as exc:  # noqa: BLE001 — observability must never raise
            _logger.warning("langfuse_send_failed", error=str(exc))
            return False
