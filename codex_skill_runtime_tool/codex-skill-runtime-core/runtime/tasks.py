from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class TaskRequest:
    agent: str
    purpose: str
    inputs: str = ""


TASK_PATTERN = re.compile(
    r"RUNTIME_TASK_REQUEST:\s*agent=(?P<agent>[\w-]+)\s*;\s*purpose=(?P<purpose>[^;\n]+)(?:;\s*inputs=(?P<inputs>[^\n]+))?",
    re.IGNORECASE,
)


def parse_task_requests(text: str) -> list[TaskRequest]:
    requests: list[TaskRequest] = []
    for match in TASK_PATTERN.finditer(text):
        requests.append(
            TaskRequest(
                agent=match.group("agent").strip(),
                purpose=match.group("purpose").strip(),
                inputs=(match.group("inputs") or "").strip(),
            )
        )
    return requests
