#!/usr/bin/env python3
"""Record one Codex lifecycle event and always fail open."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from telemetry.codex import append_observation, observation_from_hook_payload


def main() -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--output", type=Path, required=True)
    arguments, _ = parser.parse_known_args()
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            return 0
        observation = observation_from_hook_payload(payload)
        if observation is not None:
            append_observation(arguments.output, observation)
    except (OSError, ValueError, json.JSONDecodeError):
        # Hook telemetry is advisory. A recording failure must not affect Codex.
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
