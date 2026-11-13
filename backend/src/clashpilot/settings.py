"""Runtime settings, read from the environment."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# backend/src/clashpilot/settings.py -> repository root
_REPO_ROOT = Path(__file__).resolve().parents[3]

DEFAULT_COC_BASE_URL = "https://api.clashofclans.com/v1"
LOOPBACK_HOSTS = ("127.0.0.1", "localhost")
# The host name of Starlette's in-process TestClient: the frozen acceptance tests call
# create_app() with default settings through it. A foreign site cannot make a browser resolve
# this dotless name, so accepting it keeps the DNS-rebinding protection.
TEST_CLIENT_HOST = "testserver"


def _allowed_hosts(source: Mapping[str, str], host: str) -> tuple[str, ...]:
    """Host names the server answers: an explicit list, else loopback plus the bound host.

    Bound to 0.0.0.0 without a list, every name is accepted (the README warns about that mode).
    """
    explicit = source.get("CLASHPILOT_ALLOWED_HOSTS", "")
    if explicit.strip():
        return tuple(name.strip() for name in explicit.split(",") if name.strip())
    if host == "0.0.0.0":
        return ("*",)
    defaults = (*LOOPBACK_HOSTS, TEST_CLIENT_HOST)
    return defaults if host in LOOPBACK_HOSTS else (*defaults, host)


@dataclass(frozen=True)
class Settings:
    data_dir: Path = Path("data")
    coc_token: str | None = None
    coc_base_url: str = DEFAULT_COC_BASE_URL
    host: str = "127.0.0.1"
    port: int = 8000
    frontend_dist: Path = _REPO_ROOT / "frontend" / "dist"
    planner_overrides: Mapping[str, Any] = field(default_factory=dict)
    allowed_hosts: tuple[str, ...] = ("*",)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        """Build settings from CLASHPILOT_* variables, falling back to defaults."""
        source = os.environ if env is None else env
        defaults = cls()
        overrides: dict[str, Any] = {}
        if source.get("CLASHPILOT_SOLVER_MAX_TIME_S"):
            overrides["solver_max_time_s"] = float(source["CLASHPILOT_SOLVER_MAX_TIME_S"])
        host = source.get("CLASHPILOT_HOST", defaults.host)
        return cls(
            data_dir=Path(source.get("CLASHPILOT_DATA_DIR", str(defaults.data_dir))),
            coc_token=source.get("CLASHPILOT_COC_TOKEN") or None,
            coc_base_url=source.get("CLASHPILOT_COC_BASE_URL", defaults.coc_base_url),
            host=host,
            port=int(source.get("CLASHPILOT_PORT", str(defaults.port))),
            frontend_dist=Path(source.get("CLASHPILOT_FRONTEND_DIST", str(defaults.frontend_dist))),
            planner_overrides=overrides,
            allowed_hosts=_allowed_hosts(source, host),
        )
