"""Priest Life Mini App Entrypoint.

Delegates directly to app.api:app implementing TZ-tg-app.md contract.
"""
from app.api import app

__all__ = ["app"]
