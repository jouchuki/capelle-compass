"""
No-network email sender that logs instead of delivering.

Selected automatically when no Resend API key is configured (dev, test, or a
deployment that has not yet wired a provider). It writes the full message —
including any verification link in the body — to the structured log so a
developer can complete the flow without a real inbox. NEVER select this in
production: the "email" goes only to the server log.
"""

from __future__ import annotations

from capelle_platform.email.base_sender import BaseEmailSender
from capelle_platform.observability import get_logger

_logger = get_logger(__name__)


class LoggingEmailSender(BaseEmailSender):
    """Log the email at INFO and return; never touches the network."""

    async def send(
        self, *, to: str, subject: str, html: str, text: str | None = None
    ) -> None:
        """Emit the message to the log in lieu of delivery."""
        _logger.info(
            "email_logged_not_sent",
            to=to,
            subject=subject,
            body=text or html,
        )
