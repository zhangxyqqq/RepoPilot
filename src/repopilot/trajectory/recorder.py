from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class TrajectoryRecorder:
    def __init__(self, path: Path, *, run_id: str, metadata: dict[str, Any]):
        self.path = path
        self.run_id = run_id
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._sequence = 0
        self.record("run_started", metadata=metadata)

    def record(self, event_type: str, **payload: Any) -> None:
        self._sequence += 1
        event = {
            "schema_version": 1,
            "run_id": self.run_id,
            "sequence": self._sequence,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "type": event_type,
            **payload,
        }
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, sort_keys=True, ensure_ascii=False) + "\n")
