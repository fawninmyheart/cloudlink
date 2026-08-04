#!/usr/bin/env python3
import argparse
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cloudlink_client import CloudlinkAuthError, request_json  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Ask the task worker to release transient input cache files."
    )
    parser.add_argument("task_id")
    parser.add_argument(
        "--base-url",
        default=os.getenv("CLOUDLINK_INTERNAL_BASE_URL", "http://127.0.0.1:8010"),
    )
    args = parser.parse_args()
    try:
        result = request_json(
            "POST",
            f"{args.base_url.rstrip('/')}/api/internal/tasks/"
            f"{args.task_id}/release-input-cache",
            {},
        )
    except CloudlinkAuthError as exc:
        raise SystemExit(str(exc)) from exc
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
