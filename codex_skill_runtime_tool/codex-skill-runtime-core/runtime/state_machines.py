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
    if clean == "prototype":
        engine_like = "--path engine" in arguments.lower() or "godot" in arguments.lower() or "engine" in arguments.lower()
        qa_required = qa_mode == "required" or (qa_mode == "auto" and engine_like)
        phases = [
            WorkflowPhase("load-skill-agent", "runtime", True, "Load original prototype skill and prototyper agent."),
            WorkflowPhase("resolve-prototype-question", "prototyper", True, "Resolve hypothesis, path, spike mode, and scope."),
            WorkflowPhase("strict-action-loop", "runtime+prototyper", True, "Execute implementation through runtime-owned actions."),
            WorkflowPhase("required-qa", "qa-tester", qa_required, "Run independent QA evidence gate for engine prototypes."),
            WorkflowPhase("summary", "runtime", True, "Record final status and session evidence."),
        ]
        return WorkflowPlan(clean, phases)

    if clean == "team-qa":
        return WorkflowPlan(
            clean,
            [
                WorkflowPhase("load-scope", "qa-lead", True, "Find sprint or feature scope."),
                WorkflowPhase("qa-strategy", "qa-lead", True, "Classify stories and smoke-check status."),
                WorkflowPhase("test-plan", "qa-lead", True, "Produce QA plan."),
                WorkflowPhase("test-cases", "qa-tester", True, "Produce test cases for manual/integration stories."),
                WorkflowPhase("manual-qa", "runtime+human", True, "Collect PASS/FAIL/BLOCKED results."),
                WorkflowPhase("signoff", "qa-lead", True, "Produce QA sign-off verdict."),
            ],
        )

    return WorkflowPlan(
        clean,
        [
            WorkflowPhase("load-skill-agent", "runtime", True, "Load original skill and routed agent."),
            WorkflowPhase("strict-action-loop", "runtime+agent", True, "Execute workflow through runtime-owned actions."),
            WorkflowPhase("summary", "runtime", True, "Record final status and session evidence."),
        ],
    )
