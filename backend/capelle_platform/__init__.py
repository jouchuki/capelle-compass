"""
Capelle Platform — multi-user municipal data analysis platform.

Public API: import the Builder to construct and start the application.
"""

from capelle_platform.builder import AppBuilder

__version__ = "1.1.0"
__all__: list[str] = ["AppBuilder", "__version__"]
