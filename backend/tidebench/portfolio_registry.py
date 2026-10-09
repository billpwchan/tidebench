"""Immutable multi-asset strategy definitions; inputs and account policy are separate."""

import json
from contextlib import nullcontext
from decimal import Decimal
from typing import Literal

from pydantic import Field, model_validator

from .platform import PlatformError
from .schemas import InputModel
from .store import dumps, new_id, now_ms
from .strategy_program import ProStrategyInput
from .strategy_registry import canonical, digest

D = Decimal


class ManagedLeg(InputModel):
    inst_id: str = Field(pattern=r"^[A-Z0-9]{1,20}-USDT(?:-SWAP)?$")
    weight: Decimal = Field(ge=-5, le=5)
    leverage: Decimal = Field(default=1, ge=1, le=50)
    direction: Literal["long_only", "short_only", "long_short"] = "long_only"
    strategy: ProStrategyInput = Field(default_factory=ProStrategyInput)

    @model_validator(mode="after")
    def spot(self):
        if not self.inst_id.endswith("-SWAP") and (
            self.weight < 0 or self.leverage != 1 or self.direction != "long_only"
        ):
            raise ValueError("Spot legs require nonnegative weights, long-only signals and leverage one.")
        return self


class PortfolioDefinition(InputModel):
    schema_version: Literal[1] = 1
    bar: Literal["1m", "5m", "15m", "1H", "4H", "1Dutc"] = "1H"
    mode: Literal["fixed_weights", "independent_signals", "momentum", "risk_momentum", "funding_carry"] = (
        "fixed_weights"
    )
    legs: list[ManagedLeg] = Field(min_length=2, max_length=10)
    capital_pct: Decimal = Field(default=100, gt=0, le=100)
    rebalance_bars: int = Field(default=24, ge=1, le=1000)
    lookback: int = Field(default=20, ge=2, le=400)
    top_k: int = Field(default=1, ge=1, le=10)
    risk_window: int = Field(default=84, ge=10, le=400)
    vol_target_pct: Decimal = Field(default=20, gt=0, le=100)
    vol_floor_pct: Decimal = Field(default=20, gt=0, le=200)
    covariance_shrinkage: Decimal = Field(default=".25", ge=0, le=1)
    correlation_stress: Decimal = Field(default=".75", ge=0, le=1)
    carry_threshold: Decimal = Field(default=0, ge="-.01", le=".01")
    carry_window: int = Field(default=1, ge=1, le=30)
    carry_cost_settlements: int = Field(default=0, ge=0, le=300)
    carry_buffer_bps: Decimal = Field(default=0, ge=0, le=1000)
    carry_max_age_hours: int = Field(default=0, ge=0, le=168)
    max_residual_pct: Decimal = Field(default=2, ge=".01", le=100)
    failure_policy: Literal["reduce_group"] = "reduce_group"
    execution_contract: Literal["reduce_group_v1", "reduce_group_v2_allowance"] = "reduce_group_v2_allowance"

    @model_validator(mode="after")
    def construction(self):
        if len({leg.inst_id for leg in self.legs}) != len(self.legs):
            raise ValueError("A portfolio requires distinct markets.")
        if self.mode == "momentum" and (
            self.top_k > len(self.legs) or any(leg.weight < 0 for leg in self.legs)
        ):
            raise ValueError("Momentum uses nonnegative weights and top_k within the universe.")
        if self.mode == "risk_momentum" and (
            self.top_k > len(self.legs)
            or any(
                not 0 <= leg.weight <= 1 or leg.leverage != 1 or leg.direction != "long_only"
                for leg in self.legs
            )
        ):
            raise ValueError(
                "Risk momentum requires long-only unlevered legs, weight ceilings in [0,1], and top_k within the universe."
            )
        if self.mode == "funding_carry" and (
            len(self.legs) != 2
            or self.legs[0].inst_id.endswith("-SWAP")
            or self.legs[1].inst_id != self.legs[0].inst_id + "-SWAP"
            or self.legs[0].weight <= 0
            or self.legs[1].weight != self.legs[0].weight.copy_negate()
        ):
            raise ValueError(
                "Carry requires ordered spot-long / matching swap-short legs with equal notional weights."
            )
        if not any(leg.weight for leg in self.legs):
            raise ValueError("At least one portfolio weight must be nonzero.")
        return self

    def record(self):
        return canonical(self.model_dump())


class PortfolioProjectInput(InputModel):
    name: str = Field(min_length=2, max_length=100)
    hypothesis: str = Field(min_length=12, max_length=4000)
    definition: PortfolioDefinition


class PortfolioVersionInput(InputModel):
    hypothesis: str = Field(min_length=12, max_length=4000)
    definition: PortfolioDefinition
    parent_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")


class PortfolioRegistry:
    def __init__(self, store, implementation):
        self.store, self.implementation = store, implementation
        with store.write() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS portfolio_projects(id TEXT PRIMARY KEY,name TEXT NOT NULL,created_by TEXT NOT NULL,created_at INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS portfolio_versions(id TEXT PRIMARY KEY,project_id TEXT NOT NULL REFERENCES portfolio_projects(id),revision INTEGER NOT NULL,parent_id TEXT REFERENCES portfolio_versions(id),hypothesis TEXT NOT NULL,definition TEXT NOT NULL,implementation TEXT NOT NULL,content_hash TEXT NOT NULL,created_by TEXT NOT NULL,created_at INTEGER NOT NULL,UNIQUE(project_id,revision),UNIQUE(project_id,content_hash));
                CREATE INDEX IF NOT EXISTS portfolio_versions_project ON portfolio_versions(project_id,revision DESC);
            """)

    def version(self, identifier, *, conn=None):
        with nullcontext(conn) if conn is not None else self.store.read() as connection:
            row = connection.execute("SELECT * FROM portfolio_versions WHERE id=?", (identifier,)).fetchone()
        if not row:
            raise PlatformError("portfolio_version_not_found", "Portfolio version not found.", 404)
        result = dict(row) | {
            "definition": json.loads(row["definition"]),
            "implementation": json.loads(row["implementation"]),
        }
        if result["content_hash"] != digest(
            {key: result[key] for key in ("definition", "hypothesis", "implementation")}
        ):
            raise PlatformError(
                "portfolio_version_integrity", "Portfolio definition integrity check failed.", 409
            )
        return result

    def projects(self):
        with self.store.read() as conn:
            return [
                dict(row)
                for row in conn.execute(
                    "SELECT p.*,COUNT(v.id) version_count,MAX(v.revision) latest_revision FROM portfolio_projects p LEFT JOIN portfolio_versions v ON v.project_id=p.id GROUP BY p.id ORDER BY p.created_at DESC,p.id DESC LIMIT 200"
                )
            ]

    def project(self, identifier):
        with self.store.read() as conn:
            row = conn.execute("SELECT * FROM portfolio_projects WHERE id=?", (identifier,)).fetchone()
            versions = [
                v[0]
                for v in conn.execute(
                    "SELECT id FROM portfolio_versions WHERE project_id=? ORDER BY revision DESC",
                    (identifier,),
                )
            ]
            if not row:
                raise PlatformError("portfolio_not_found", "Portfolio project not found.", 404)
            return dict(row) | {"versions": [self.version(v, conn=conn) for v in versions]}

    def create_project(self, name, hypothesis, definition, actor):
        name = name.strip()
        if not 2 <= len(name) <= 100:
            raise PlatformError("portfolio_name", "Use a portfolio name of 2–100 characters.", 422)
        identifier = new_id()
        with self.store.write() as conn:
            conn.execute(
                "INSERT INTO portfolio_projects VALUES(?,?,?,?)", (identifier, name, actor, now_ms())
            )
            version = self.create_version(identifier, hypothesis, definition, actor, conn=conn)
        return self.project(identifier) | {"version": version}

    def create_version(self, project_id, hypothesis, definition, actor, parent_id=None, *, conn=None):
        definition = PortfolioDefinition.model_validate(definition).record()
        hypothesis = hypothesis.strip()
        if not 12 <= len(hypothesis) <= 4000:
            raise PlatformError(
                "portfolio_hypothesis", "Describe the economic hypothesis in 12–4000 characters.", 422
            )
        identity = digest(
            {"definition": definition, "hypothesis": hypothesis, "implementation": self.implementation}
        )
        with nullcontext(conn) if conn is not None else self.store.write() as connection:
            if not connection.execute(
                "SELECT 1 FROM portfolio_projects WHERE id=?", (project_id,)
            ).fetchone():
                raise PlatformError("portfolio_not_found", "Portfolio project not found.", 404)
            if parent_id and self.version(parent_id, conn=connection)["project_id"] != project_id:
                raise PlatformError("portfolio_parent", "Parent version must belong to this project.", 409)
            existing = connection.execute(
                "SELECT id FROM portfolio_versions WHERE project_id=? AND content_hash=?",
                (project_id, identity),
            ).fetchone()
            if existing:
                return self.version(existing[0], conn=connection)
            revision = connection.execute(
                "SELECT COALESCE(MAX(revision),0)+1 FROM portfolio_versions WHERE project_id=?", (project_id,)
            ).fetchone()[0]
            identifier = new_id()
            connection.execute(
                "INSERT INTO portfolio_versions VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    identifier,
                    project_id,
                    revision,
                    parent_id,
                    hypothesis,
                    dumps(definition),
                    dumps(self.implementation),
                    identity,
                    actor,
                    now_ms(),
                ),
            )
            self.store.audit(
                connection,
                "system",
                "portfolio.version_created",
                "Immutable portfolio definition created",
                {
                    "project_id": project_id,
                    "version_id": identifier,
                    "revision": revision,
                    "content_hash": identity,
                    "actor": actor,
                },
            )
            return self.version(identifier, conn=connection)

    def validate_binding(self, config, packages):
        identifier = config.get("portfolio_version_id")
        if not identifier:
            return None
        version = self.version(identifier)
        if version["implementation"]["code_fingerprint"] != self.implementation["code_fingerprint"]:
            raise PlatformError(
                "portfolio_implementation_changed",
                "Review a new version for the installed implementation.",
                409,
            )
        definition = version["definition"]
        if "execution_contract" not in definition:
            raise PlatformError(
                "portfolio_execution_legacy",
                "Review a new portfolio revision with an explicit shared execution contract; the stored legacy version is retained unchanged.",
                409,
            )
        if config["hypothesis"].strip() != version["hypothesis"]:
            raise PlatformError(
                "portfolio_binding", "Research hypothesis must match its immutable portfolio version.", 409
            )
        for key in (
            "mode",
            "failure_policy",
            "execution_contract",
            "max_residual_pct",
            "rebalance_bars",
            "lookback",
            "top_k",
            "risk_window",
            "vol_target_pct",
            "vol_floor_pct",
            "covariance_shrinkage",
            "correlation_stress",
            "carry_window",
            "carry_cost_settlements",
            "carry_buffer_bps",
            "carry_max_age_hours",
            "carry_threshold",
            "capital_pct",
        ):
            observed = (
                canonical(D(str(config[key])))
                if key
                in {
                    "carry_threshold",
                    "carry_buffer_bps",
                    "capital_pct",
                    "max_residual_pct",
                    "vol_target_pct",
                    "vol_floor_pct",
                    "covariance_shrinkage",
                    "correlation_stress",
                }
                else config[key]
            )
            if observed != definition[key]:
                raise PlatformError(
                    "portfolio_binding",
                    "Research construction must match its immutable portfolio version.",
                    409,
                )
        if any(p["bar"] != definition["bar"] for p in packages) or len(packages) != len(definition["legs"]):
            raise PlatformError(
                "portfolio_binding",
                "Research inputs must match the portfolio interval and complete universe.",
                409,
            )
        for leg, expected, package in zip(config["legs"], definition["legs"], packages, strict=True):
            observed = canonical(
                ManagedLeg.model_validate(
                    {key: leg[key] for key in ("weight", "leverage", "direction", "strategy")}
                    | {"inst_id": package["inst_id"]}
                ).model_dump()
            )
            if observed != expected:
                raise PlatformError(
                    "portfolio_binding",
                    "Each research leg must match its saved market, weight and policy.",
                    409,
                )
        return version
