"""The ClashPilot application with a clock the end-to-end journey can move forward.

Only the clock differs: `POST /__e2e/advance?seconds=N` adds N seconds to it. The journey needs it
because a late start ("retard d'une heure") is an event dated after the planned start, and the
server refuses an event dated in the future: time has to pass between the page and the click.
"""

from __future__ import annotations

import time

from fastapi import FastAPI

from clashpilot.api.app import create_app as build_app
from clashpilot.settings import Settings


def create_app() -> FastAPI:
    offset = {"seconds": 0}
    app = build_app(Settings.from_env(), clock=lambda: int(time.time()) + offset["seconds"])

    @app.post("/__e2e/advance")
    def advance(seconds: int) -> dict[str, int]:
        offset["seconds"] += max(0, seconds)
        return {"offset": offset["seconds"]}

    return app
