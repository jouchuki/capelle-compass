"""Analysis modes supported by Compass."""
from typing import Literal, get_args

Mode = Literal['groeikern', 'jeugdzorg']
SUPPORTED_MODES: tuple[Mode, ...] = get_args(Mode)
DEFAULT_MODE: Mode = 'groeikern'
