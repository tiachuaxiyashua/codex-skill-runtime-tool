from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
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
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    scratchpad_dir: str = ""

    @property
    def latest_output(self) -> str:
        for turn in reversed(self.turns):
            if turn.get("output"):
                return turn["output"]
        return ""


class WorkerRegistry:
    def __init__(self, runner: WorkerRunner, *, session_dir: Path | None = None) -> None:
        self.runner = runner
        self.session_dir = session_dir
        self._records: dict[str, WorkerRecord] = {}
        self._names: dict[str, str] = {}
        self._counter = 0
        self._load()

    def spawn(self, *, agent: str, purpose: str, prompt: str, name: str | None = None) -> WorkerRecord:
        self._counter += 1
        worker_id = f"worker-{self._counter:03d}"
        scratchpad_dir = self._scratchpad_dir(worker_id)
        output = self.runner(agent, purpose, _with_scratchpad(prompt, scratchpad_dir), self._counter)
        record = WorkerRecord(
            id=worker_id,
            agent=agent,
            purpose=purpose,
            name=name,
            scratchpad_dir=str(scratchpad_dir) if scratchpad_dir is not None else "",
            turns=[{"prompt": prompt, "output": output}],
        )
        self._records[worker_id] = record
        if name:
            self._names[name] = worker_id
        self._save()
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
            f"Worker scratchpad: `{record.scratchpad_dir}`\n\n"
            f"New instruction:\n{prompt}"
        )
        output = self.runner(record.agent, f"Continue {record.purpose}", continuation, len(self._records) + 1)
        record.turns.append({"prompt": prompt, "output": output})
        record.status = "completed"
        record.updated_at = datetime.now().isoformat(timespec="seconds")
        self._save()
        return record

    def stop(self, *, to: str, reason: str = "") -> WorkerRecord:
        record = self._find(to)
        record.status = "stopped"
        record.turns.append({"prompt": f"TASK_STOP: {reason}", "output": "Worker marked stopped by runtime."})
        record.updated_at = datetime.now().isoformat(timespec="seconds")
        self._save()
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
                    "updated_at": record.updated_at,
                    "scratchpad_dir": record.scratchpad_dir,
                }
            )
        return rows

    def _find(self, key: str) -> WorkerRecord:
        worker_id = self._names.get(key, key)
        if worker_id not in self._records:
            raise KeyError(f"worker not found: {key}")
        return self._records[worker_id]

    def _save(self) -> None:
        if self.session_dir is None:
            return
        path = self.session_dir / "workers.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "updated_at": datetime.now().isoformat(timespec="seconds"),
                    "counter": self._counter,
                    "workers": [asdict(record) for record in self._records.values()],
                    "names": self._names,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    def _load(self) -> None:
        if self.session_dir is None:
            return
        path = self.session_dir / "workers.json"
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        except (OSError, ValueError):
            return
        if not isinstance(data, dict):
            return
        self._counter = int(data.get("counter") or 0)
        names = data.get("names")
        if isinstance(names, dict):
            self._names = {str(key): str(value) for key, value in names.items()}
        workers = data.get("workers")
        if not isinstance(workers, list):
            return
        for item in workers:
            if not isinstance(item, dict) or not item.get("id"):
                continue
            turns = item.get("turns")
            record = WorkerRecord(
                id=str(item.get("id")),
                agent=str(item.get("agent") or ""),
                purpose=str(item.get("purpose") or ""),
                name=str(item.get("name")) if item.get("name") else None,
                status=str(item.get("status") or "completed"),
                turns=[turn for turn in turns if isinstance(turn, dict)] if isinstance(turns, list) else [],
                updated_at=str(item.get("updated_at") or datetime.now().isoformat(timespec="seconds")),
                scratchpad_dir=str(item.get("scratchpad_dir") or ""),
            )
            self._records[record.id] = record
            try:
                suffix = int(record.id.rsplit("-", 1)[-1])
            except ValueError:
                suffix = 0
            self._counter = max(self._counter, suffix)

    def _scratchpad_dir(self, worker_id: str) -> Path | None:
        if self.session_dir is None:
            return None
        path = self.session_dir / "workers" / worker_id / "scratchpad"
        path.mkdir(parents=True, exist_ok=True)
        readme = path / "README.md"
        if not readme.exists():
            readme.write_text(
                "Worker scratchpad for temporary notes, probes, and intermediate outputs.\n",
                encoding="utf-8",
            )
        return path


def worker_scratchpad_context(session_or_dir: Path | str, *, max_files: int = 20, max_chars: int = 12000) -> str:
    session_dir = Path(session_or_dir)
    workers_path = session_dir / "workers.json"
    if not workers_path.exists():
        return ""
    try:
        data = json.loads(workers_path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError):
        return ""
    workers = data.get("workers") if isinstance(data, dict) else []
    if not isinstance(workers, list):
        return ""
    lines = ["## Worker Scratchpads", ""]
    used = 0
    count = 0
    for worker in workers:
        if not isinstance(worker, dict):
            continue
        scratchpad = Path(str(worker.get("scratchpad_dir") or ""))
        if not scratchpad.exists() or not scratchpad.is_dir():
            continue
        lines.append(f"### {worker.get('id', '')} {worker.get('name', '')}".strip())
        lines.append(f"Scratchpad: `{scratchpad}`")
        for path in sorted(p for p in scratchpad.rglob("*") if p.is_file())[:max_files]:
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            remaining = max_chars - used
            if remaining <= 0:
                break
            preview = text[: min(remaining, 1000)]
            used += len(preview)
            lines.extend(["", f"- `{path}`", "```text", preview, "```"])
            count += 1
        lines.append("")
    if count == 0:
        return ""
    return "\n".join(lines).strip()


def _with_scratchpad(prompt: str, scratchpad_dir: Path | None) -> str:
    if scratchpad_dir is None:
        return prompt
    return (
        f"{prompt}\n\n"
        "Runtime worker scratchpad:\n"
        f"- Use `{scratchpad_dir}` for temporary notes, probes, and intermediate outputs for this worker.\n"
        "- Keep durable project facts in runtime memory tools, not only in scratchpad files.\n"
    )
