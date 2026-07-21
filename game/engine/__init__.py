"""
The rules engine: a small, Django-agnostic package.

It knows nothing about the database, HTTP, or how results are delivered. It takes
plain dataclasses in (KeywordSpec, TeamBid) and returns plain dataclasses out
(ResultRow). This is what keeps the game economics identical whether results reach
the browser by polling (Phase 1-2) or by WebSockets in the live-ticker sprint.
"""
from .economics import KeywordSpec, ResultRow, TeamBid
from .resolve import resolve_keyword

__all__ = ["KeywordSpec", "TeamBid", "ResultRow", "resolve_keyword"]
