from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class WorkflowPhase:
    id: str
    owner: str
    required: bool
    description: str


@dataclass(frozen=True)
class WorkflowPlan:
    command: str
    phases: list[WorkflowPhase]

    def to_dict(self) -> dict[str, object]:
        return {"command": self.command, "phases": [asdict(phase) for phase in self.phases]}


def build_workflow_plan(command: str, arguments: str, qa_mode: str) -> WorkflowPlan:
    clean = command[1:] if command.startswith("/") else command
    return WorkflowPlan(
        clean,
        [
            WorkflowPhase("load-skill-agent", "runtime", True, "Load the requested skill and routed agent from configured skill repositories."),
            WorkflowPhase("strict-action-loop", "runtime+agent", True, "Execute the workflow through runtime-owned actions when strict tools are enabled."),
            WorkflowPhase("question-pause", "runtime+user", False, "Pause and persist a question when required inputs are missing."),
            WorkflowPhase("qa-gate", "runtime+qa-agent", qa_mode == "required", "Run an independent QA gate when requested by runtime configuration."),
            WorkflowPhase("summary", "runtime", True, "Record final status, transcript, task tree, artifacts, and memory summary."),
        ],
    )
