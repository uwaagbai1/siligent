"""An append-only log of every action taken on the bench, refused or not."""

from __future__ import annotations

import json
import time
from pathlib import Path


class ActionLog:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, action: str, outcome: str, **fields: object) -> dict:
        entry = {"time": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "action": action, "outcome": outcome, **fields}
        with self.path.open("a") as f:
            f.write(json.dumps(entry, default=str) + "\n")
        return entry

    def tail(self, n: int = 20) -> list[dict]:
        if not self.path.exists():
            return []
        lines = self.path.read_text().splitlines()[-n:]
        return [json.loads(line) for line in lines if line.strip()]
