from __future__ import annotations

import json
import re
from typing import Any


def parse_json_response(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if not stripped:
        raise ValueError("empty JSON response")

    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", stripped, flags=re.DOTALL)
        if not match:
            raise
        parsed = json.loads(match.group(1))

    if not isinstance(parsed, dict):
        raise ValueError("JSON response must be an object")
    return parsed
