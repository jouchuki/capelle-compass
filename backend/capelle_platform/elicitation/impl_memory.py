"""
In-memory elicitation registry backed by a plain asyncio dict.

Suitable for single-process deployments (the common case). All dict
operations are effectively thread-safe in CPython's GIL and run on a
single asyncio event loop, so no additional locking is required.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from capelle_platform.elicitation.base_registry import BaseElicitationRegistry
from capelle_platform.observability import get_logger

_logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class _PendingQuestion:
    """A pending question's Future plus the id of the user that owns it."""

    future: asyncio.Future[str]
    owner_user_id: str


class InMemoryElicitationRegistry(BaseElicitationRegistry):
    """
    ``BaseElicitationRegistry`` backed by ``dict[str, _PendingQuestion]``.

    Thread / async-safe for single-process asyncio deployments. The dict is
    private; only :meth:`register`, :meth:`resolve`, and :meth:`discard`
    mutate it. Each entry binds the awaiting ``Future`` to the ``user_id``
    that owns the question so a different authenticated user cannot inject an
    answer (API-1).
    """

    def __init__(self) -> None:
        """Initialise with an empty pending-question dict."""
        self._pending: dict[str, _PendingQuestion] = {}

    async def register(
        self, question_id: str, owner_user_id: str
    ) -> asyncio.Future[str]:
        """
        Create a new ``Future`` for ``question_id`` bound to ``owner_user_id``.

        Args:
            question_id: Unique identifier for the pending question.
            owner_user_id: The id of the user that may resolve this question.

        Returns:
            The new unresolved ``Future``.
        """
        loop = asyncio.get_running_loop()
        future: asyncio.Future[str] = loop.create_future()
        self._pending[question_id] = _PendingQuestion(
            future=future, owner_user_id=owner_user_id
        )
        _logger.info(
            "elicitation_registered",
            question_id=question_id,
            pending_count=len(self._pending),
        )
        return future

    def resolve(
        self, question_id: str, answer: str, requesting_user_id: str
    ) -> bool:
        """
        Deliver ``answer`` to ``question_id`` iff the caller owns it.

        Args:
            question_id: The ID previously passed to :meth:`register`.
            answer: The user-supplied answer string.
            requesting_user_id: The authenticated caller's id.

        Returns:
            ``True`` when the result was set; ``False`` when the ID is unknown,
            owned by a different user, or the ``Future`` is already done.
        """
        entry = self._pending.get(question_id)
        if entry is None:
            _logger.info(
                "elicitation_resolve_unknown",
                question_id=question_id,
            )
            return False
        if entry.owner_user_id != requesting_user_id:
            # Do not leak existence — same negative outcome as "unknown".
            _logger.warning(
                "elicitation_resolve_owner_mismatch",
                question_id=question_id,
                requesting_user_id=requesting_user_id,
            )
            return False
        future = entry.future
        if future.done():
            _logger.info(
                "elicitation_resolve_already_done",
                question_id=question_id,
            )
            return False
        future.set_result(answer)
        _logger.info(
            "elicitation_resolved",
            question_id=question_id,
            answer_length=len(answer),
        )
        return True

    def discard(self, question_id: str) -> None:
        """
        Remove the entry for ``question_id`` from the registry.

        Args:
            question_id: The ID to remove. Idempotent if absent.
        """
        removed = self._pending.pop(question_id, None)
        if removed is not None:
            _logger.info(
                "elicitation_discarded",
                question_id=question_id,
            )
