"""Durable forward-only synthetic time; unrelated to production wall-clock health."""

from .platform import PlatformError
from .store import now_ms

ANCHOR = 1767225600000
MAX_TIME = 4102444800000


class SimulationClock:
    def __init__(self, store):
        self.store = store
        with store.write() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS simulation_clock(id INTEGER PRIMARY KEY CHECK(id=1),market_ts INTEGER NOT NULL,wall_ts INTEGER NOT NULL,speed INTEGER NOT NULL,revision INTEGER NOT NULL)"
            )
            conn.execute("INSERT OR IGNORE INTO simulation_clock VALUES(1,?,?,0,0)", (ANCHOR, now_ms()))

    @staticmethod
    def value(row, wall):
        return min(MAX_TIME, row["market_ts"] + max(0, wall - row["wall_ts"]) * row["speed"])

    def status(self):
        with self.store.read() as conn:
            row = dict(conn.execute("SELECT * FROM simulation_clock WHERE id=1").fetchone())
        return {
            "market_ts": self.value(row, now_ms()),
            "speed": row["speed"],
            "paused": row["speed"] == 0,
            "revision": row["revision"],
            "synthetic": True,
        }

    def now(self):
        return self.status()["market_ts"]

    def change(self, *, speed=None, step_ms=0, expected_revision, actor):
        if (
            (speed is not None and (type(speed) is not int or not 0 <= speed <= 3600))
            or type(step_ms) is not int
            or not 0 <= step_ms <= 86_400_000
        ):
            raise PlatformError(
                "clock_input", "Speed must be 0–3600; each step may advance at most one day.", 422
            )
        with self.store.write() as conn:
            row = dict(conn.execute("SELECT * FROM simulation_clock WHERE id=1").fetchone())
            if expected_revision != row["revision"]:
                raise PlatformError(
                    "clock_changed", "Simulation clock changed. Refresh before applying another command.", 409
                )
            if step_ms and row["speed"]:
                raise PlatformError("clock_running", "Pause the simulation clock before stepping.", 409)
            wall = now_ms()
            market = self.value(row, wall) + step_ms
            if market > MAX_TIME:
                raise PlatformError("clock_range", "Simulation clock exceeds its supported range.", 422)
            conn.execute(
                "UPDATE simulation_clock SET market_ts=?,wall_ts=?,speed=?,revision=revision+1 WHERE id=1",
                (market, wall, row["speed"] if speed is None else speed),
            )
            self.store.audit(
                conn,
                "example",
                "pro.clock_changed",
                "Synthetic time advanced or speed changed",
                {"market_ts": market, "speed": row["speed"] if speed is None else speed, "actor": actor},
            )
        return self.status()
