"""Daily spend guard: a small JSON state file keyed by local date. When the
day's estimated spend reaches the cap, model calls stop and the pipeline
fails safe to Confirm."""
import json
import os
from datetime import date


class SpendGuard:
    def __init__(self, state_path: str, cap_usd: float):
        self.state_path = state_path
        self.cap_usd = cap_usd

    def _load(self) -> dict:
        try:
            with open(self.state_path) as fh:
                data = json.load(fh)
            if data.get("date") == str(date.today()):
                return data
        except (OSError, ValueError):
            pass
        return {"date": str(date.today()), "spent_usd": 0.0, "calls": 0}

    def spent(self) -> float:
        return float(self._load()["spent_usd"])

    def allowed(self) -> bool:
        return self.spent() < self.cap_usd

    def record(self, cost_usd: float) -> None:
        data = self._load()
        data["spent_usd"] = float(data["spent_usd"]) + cost_usd
        data["calls"] = int(data["calls"]) + 1
        os.makedirs(os.path.dirname(self.state_path) or ".", exist_ok=True)
        with open(self.state_path, "w") as fh:
            json.dump(data, fh)
