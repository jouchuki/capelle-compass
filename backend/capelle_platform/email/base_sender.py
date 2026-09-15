"""
Transactional email transport interface.

The decision layer (e.g. :class:`AuthHandler`) depends only on this ABC, so
the provider (Resend, SMTP, SES, or a no-op logger) is swappable at wiring
time without touching callers. Implementations must be safe to call from the
async request path and must never raise for a *delivery* failure that the
caller has already decided is non-fatal — they raise only for programmer
error (e.g. a misconfigured transport).
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class BaseEmailSender(ABC):
    """Send a single transactional email. One method, one responsibility."""

    @abstractmethod
    async def send(
        self, *, to: str, subject: str, html: str, text: str | None = None
    ) -> None:
        """
        Deliver an email to ``to``.

        Args:
            to: Recipient address.
            subject: Subject line.
            html: HTML body.
            text: Optional plain-text alternative; implementations may
                synthesise one when omitted.

        Raises:
            EmailDeliveryError: when the transport rejects the message.
        """


class EmailDeliveryError(RuntimeError):
    """Raised when an email transport fails to accept a message."""
