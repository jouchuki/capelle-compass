"""
Abstract base for the elicitation registry.

The elicitation registry manages in-flight mid-run questions. When an agent
calls the ``capelle-ask`` CLI, the platform registers a Future keyed by a
``question_id`` and suspends until the user answers via the browser.

Implementations may back this with an asyncio dict (single-process) or an
external store (multi-instance).
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod


class BaseElicitationRegistry(ABC):
    """
    Contract for tracking pending agent questions awaiting user answers.

    A question moves through three lifecycle states:

    1. **Registered** — :meth:`register` creates a ``Future`` and stores it.
    2. **Resolved** — :meth:`resolve` delivers the answer via the ``Future``.
    3. **Discarded** — :meth:`discard` removes the entry after delivery or
       timeout so memory is reclaimed.
    """

    async def start(self) -> None:
        """Begin any background machinery (e.g. a broker consumer).

        No-op by default so single-process implementations need not override
        it. The Builder calls this for every registry uniformly, mirroring
        the WebSocket hub lifecycle.
        """

    async def stop(self) -> None:
        """Tear down background machinery. No-op by default. Idempotent."""

    @abstractmethod
    async def register(self, question_id: str, owner_user_id: str) -> asyncio.Future[str]:
        """
        Create and store a ``Future`` bound to its owning user.

        The caller awaits the returned ``Future``; when the *owning* user
        answers, :meth:`resolve` sets its result. The ``owner_user_id`` is
        retained so :meth:`resolve` can reject answers from any other
        authenticated user (API-1 authorization boundary).

        Args:
            question_id: Unique identifier for this question (opaque string,
                typically ``uuid4().hex``).
            owner_user_id: The id of the user whose in-flight analysis raised
                this question. Only this user may resolve it.

        Returns:
            An unresolved ``Future[str]`` that will receive the answer.
        """

    @abstractmethod
    def resolve(
        self, question_id: str, answer: str, requesting_user_id: str
    ) -> bool:
        """
        Deliver an answer to a waiting question, enforcing ownership.

        Sets the result on the stored ``Future`` only if the question is
        still pending AND ``requesting_user_id`` matches the ``owner_user_id``
        captured at :meth:`register` time. A mismatch is rejected without
        revealing whether the ``question_id`` exists (returns ``False``),
        closing the cross-user answer-injection hole (API-1).

        Args:
            question_id: The ID that was passed to :meth:`register`.
            answer: The user-supplied answer string.
            requesting_user_id: The id of the authenticated caller submitting
                the answer.

        Returns:
            ``True`` if the ``Future`` was found, owned by the caller, and its
            result set; ``False`` if the ID is unknown, owned by a different
            user, or the ``Future`` is already done.
        """

    @abstractmethod
    def discard(self, question_id: str) -> None:
        """
        Remove the entry for ``question_id`` from the registry.

        Called in a ``finally`` block after the answer is consumed or a
        timeout fires. Idempotent — safe to call on an already-absent ID.

        Args:
            question_id: The ID to remove.
        """
