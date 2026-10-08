"""Content identities for the installed research implementation and its results."""

from __future__ import annotations

import hashlib
import platform
from pathlib import Path

from . import __version__
from .store import dumps


def research_identity() -> dict:
    directory = Path(__file__).parent
    modules = {}
    for name in (
        "engine.py",
        "schemas.py",
        "catalog.py",
        "instrument_observations.py",
        "market.py",
        "data_packages.py",
        "store.py",
        "pro_api.py",
        "derivatives.py",
        "pro_research.py",
        "pro_service.py",
        "strategy_registry.py",
        "strategy_risk.py",
        "research_budget.py",
        "research_process.py",
        "research_artifacts.py",
        "research_governance.py",
        "research_protocol.py",
        "portfolio_registry.py",
        "portfolio_releases.py",
        "managed_portfolios.py",
        "portfolio_research.py",
        "portfolio_targets.py",
        "portfolio_construction.py",
        "pro_execution.py",
        "contributions.py",
        "strategy_program.py",
        "strategy_models.py",
        "forward_history.py",
        "simulation_clock.py",
        "provenance.py",
    ):
        modules[name] = hashlib.sha256((directory / name).read_bytes()).hexdigest()
    return {
        "schema_version": 1,
        "application_version": __version__,
        "code_fingerprint": hashlib.sha256(dumps(modules).encode()).hexdigest(),
        "modules": modules,
        "python": platform.python_version(),
        "implementation": platform.python_implementation(),
        "accounting": {"precision": 50, "rounding": "ROUND_HALF_EVEN"},
    }


def serialized_result(result: dict) -> tuple[str, str]:
    payload = dumps(result)
    return payload, hashlib.sha256(payload.encode()).hexdigest()
