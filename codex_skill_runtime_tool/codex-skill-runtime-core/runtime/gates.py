from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class GateResult:
    name: str
    status: str
    reason: str


def evaluate_qa_report(text: str) -> GateResult:
    normalized = text.upper()
    verdict_match = re.search(r"VERDICT\s*:\s*(PASS|FAIL|BLOCKED|PASS WITH WARNINGS)", normalized)
    if not verdict_match:
        return GateResult("QA", "BLOCKED", "QA output did not include a VERDICT line.")

    verdict = verdict_match.group(1)
    if verdict == "PASS":
        if "EVIDENCE MATRIX" not in normalized:
            return GateResult("QA", "BLOCKED", "PASS verdict lacked an evidence matrix.")
        return GateResult("QA", "PASS", "QA returned PASS with evidence.")
    if verdict == "PASS WITH WARNINGS":
        return GateResult("QA", "WARN", "QA returned PASS WITH WARNINGS.")
    return GateResult("QA", verdict, f"QA returned {verdict}.")
