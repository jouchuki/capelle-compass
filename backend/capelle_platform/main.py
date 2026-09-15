"""
Application entry point.

Constructs the app via the Builder and runs it with uvicorn.
This file is intentionally minimal — all wiring lives in builder.py.
"""

from __future__ import annotations

import uvicorn

from capelle_platform.builder import AppBuilder
from capelle_platform.settings import Settings


def create_app() -> object:
    """
    Factory function for creating the FastAPI application.

    Used by uvicorn when running via module path:
        uvicorn capelle_platform.main:create_app --factory
    """
    settings = Settings()
    builder = AppBuilder(settings)
    return builder.build()


if __name__ == "__main__":
    settings = Settings()
    builder = AppBuilder(settings)
    app = builder.build()
    uvicorn.run(
        app,
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
    )
