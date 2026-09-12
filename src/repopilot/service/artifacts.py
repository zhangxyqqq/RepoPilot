"""Artifact publication boundary; execution staging/fencing remains local.

The API reads metadata already published to PostgreSQL. A future object-store
adapter can publish summaries/references here; it cannot replace execution locks.
"""
import json
from pathlib import Path
from typing import Protocol
from uuid import UUID


class ArtifactPublisher(Protocol):
    def metadata(self, run_id: str) -> dict: ...


class LocalArtifacts:
    def __init__(self, root: Path):
        self.root = root.resolve()

    def metadata(self, run_id: str) -> dict:
        reference = str(UUID(str(run_id)))
        path = (self.root/reference/'run.json').resolve(strict=True)
        if not path.is_relative_to(self.root):
            raise ValueError('artifact escapes approved root')
        return {'trace_summary':json.loads(path.read_text())['trace_summary'],
                'trajectory_path':f'{reference}/trajectory.jsonl'}
