from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable


WorkerRunner = Callable[[str, str, str, int], str]


@dataclass
class WorkerRecord:
    id: str
    agent: str
    purpose: str
    name: str | None = None
    status: str = "completed"
    turns: list[dict[str, str]] = field(default_factory=list)

    @property
    def latest_output(self) -> str:
        for turn in reversed(self.turns):
            if turn.get("output"):
                return turn["output"]
        return ""


class WorkerRegistry:
    def __init__(self, runner: WorkerRunner) -> None:
        self.runner = runner
        self._records: dict[str, WorkerRecord] = {}
        self._names: dict[str, str] = {}
        self._counter = 0

    def spawn(self, *, agent: str, purpose: str, prompt: str, name: str | None = None) -> WorkerRecord:
        self._counter += 1
        worker_id = f"worker-{self._counter:03d}"
        output = self.runner(agent, purpose, prompt, self._counter)
        record = WorkerRecord(
            id=worker_id,
            agent=agent,
            purpose=purpose,
            name=name,
            turns=[{"prompt": prompt, "output": output}],
        )
        self._records[worker_id] = record
        if name:
            self._names[name] = worker_id
        return record

    def send(self, *, to: str, prompt: str) -> WorkerRecord:
        record = self._find(to)
        if record.status == "stopped":
            record.status = "continued-after-stop"
        history = "\n\n".join(
            f"Previous prompt:\n{turn.get('prompt', '')}\n\nPrevious output:\n{turn.get('output', '')}"
            for turn in record.turns[-4:]
        )
        continuation = (
            "Continue the existing worker task with preserved context.\n\n"
            f"Worker id: {record.id}\n"
            f"Original purpose: {record.purpose}\n\n"
            f"Recent worker context:\n{history}\n\n"
            f"New instruction:\n{prompt}"
        )
        output = self.runner(record.agent, f"Continue {record.purpose}", continuation, len(self._records) + 1)
        record.turns.append({"prompt": prompt, "output": output})
        record.status = "completed"
        return record

    def stop(self, *, to: str, reason: str = "") -> WorkerRecord:
        record = self._find(to)
        record.status = "stopped"
        record.turns.append({"prompt": f"TASK_STOP: {reason}", "output": "Worker marked stopped by runtime."})
        return record

    def describe(self) -> list[dict[str, str]]:
        rows = []
        for record in self._records.values():
            rows.append(
                {
                    "id": record.id,
                    "name": record.name or "",
                    "agent": record.agent,
                    "purpose": record.purpose,
                    "status": record.status,
                    "latest_output": record.latest_output[:1000],
                }
            )
        return rows

    def _find(self, key: str) -> WorkerRecord:
        worker_id = self._names.get(key, key)
        if worker_id not in self._records:
            raise KeyError(f"worker not found: {key}")
        return self._records[worker_id]
