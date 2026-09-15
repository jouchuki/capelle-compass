"""
Public API for platform domain models.

All model classes are re-exported here so consumers import from
`capelle_platform.models` rather than reaching into submodules.
"""

from capelle_platform.models.user import User, UserCreate, TokenResponse
from capelle_platform.models.session import ChatSession, ChatSessionCreate
from capelle_platform.models.message import (
    ChatMessage,
    ChatMessageCreate,
    MessageRole,
    MessageStatus,
)
from capelle_platform.models.job import JobMessage, JobStatus
from capelle_platform.models.mode import DEFAULT_MODE, Mode, SUPPORTED_MODES

__all__: list[str] = [
    "User",
    "UserCreate",
    "TokenResponse",
    "ChatSession",
    "ChatSessionCreate",
    "ChatMessage",
    "ChatMessageCreate",
    "MessageRole",
    "MessageStatus",
    "JobMessage",
    "JobStatus",
    "Mode",
    "SUPPORTED_MODES",
    "DEFAULT_MODE",
]
