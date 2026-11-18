"""ASGI entrypoint: ``uvicorn meip.main:app``."""

from meip.app import create_app

app = create_app()
