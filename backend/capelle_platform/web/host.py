"""Session namespace used by Compass.
"""
from typing import Final, Literal

Domain = Literal['capelle']
DEFAULT_DOMAIN: Final[Domain] = 'capelle'

def domain_for_host(host: str) -> Domain:
    """Return this service's namespace for every deployment hostname."""
    return DEFAULT_DOMAIN
