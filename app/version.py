import re
from typing import Optional


CLOUDLINK_VERSION = "2026.07.06.3"
MINIMUM_WORKER_VERSION = "2026.07.06.3"


def numeric_version_parts(value: str) -> Optional[tuple[int, ...]]:
    text = str(value or "").strip()
    if not re.fullmatch(r"\d+(?:[._-]\d+)*", text):
        return None
    return tuple(int(part) for part in re.split(r"[._-]", text))


def version_at_least(candidate: str, minimum: str) -> bool:
    candidate_text = str(candidate or "").strip()
    minimum_text = str(minimum or "").strip()
    if not candidate_text or not minimum_text:
        return False

    candidate_parts = numeric_version_parts(candidate_text)
    minimum_parts = numeric_version_parts(minimum_text)
    if candidate_parts is None or minimum_parts is None:
        return candidate_text == minimum_text

    width = max(len(candidate_parts), len(minimum_parts))
    normalized_candidate = candidate_parts + (0,) * (width - len(candidate_parts))
    normalized_minimum = minimum_parts + (0,) * (width - len(minimum_parts))
    return normalized_candidate >= normalized_minimum
