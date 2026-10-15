#!/usr/bin/env python3
"""
Replay cc-safety-net's harvested verdict labels through the local Bash guards.

Each upstream literal goes through the four deterministic hooks in-process;
nothing is executed and no decision model is called. Deferred commands are
only checked for model eligibility (bash_risk_judge's pre-filter). The result
is a label comparison, not a vulnerability rate; see
docs/cc-safety-net-coverage.md for how to read it.

The upstream checkout is not vendored. Clone it at the pinned commit first:

    git clone https://github.com/kenryu42/cc-safety-net.git scratchpad/cc-safety-net
    git -C scratchpad/cc-safety-net checkout 0626be7a791c17a0daa67ae90a899372c667056d

Usage:
    python3 scripts/replay_ccsafety.py
    python3 scripts/replay_ccsafety.py --upstream PATH --out DIR
"""

import argparse
import collections
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = Path("tests/fixtures/gate/harvested-verdicts.jsonl")
LABEL_COLUMN = "work/standard"
LABELS = ("allow", "deny", "fail-closed")

sys.path.insert(0, str(ROOT / "hooks"))
import bash_risk_judge
import config_guard
import dangerous_commands
import rm_rf_guard
import secrets_guard
import shell_sources
from shell_parse import parse

GUARDS = (
    ("config", config_guard.run),
    ("dangerous", dangerous_commands.run),
    ("rm", rm_rf_guard.run),
    ("secrets", secrets_guard.run),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--upstream",
        type=Path,
        default=ROOT / "scratchpad/cc-safety-net",
        help="cc-safety-net checkout (default: scratchpad/cc-safety-net)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=ROOT / "scratchpad",
        help="directory for the replay JSONL and summary (default: scratchpad)",
    )
    return parser.parse_args()


def load_rows(upstream: Path) -> list[dict]:
    # JSONL records end at "\n" only; literals may hold U+2028 and other splitlines() breaks.
    fixture = upstream / FIXTURE
    if not fixture.is_file():
        sys.exit(
            f"missing {fixture}; clone cc-safety-net as shown in this script's docstring"
        )
    return [json.loads(line) for line in fixture.read_text().split("\n") if line]


def decide(command: str, cwd: Path) -> dict:
    """Run every guard; the strongest decision wins, as in the live hook chain."""
    data = {
        "tool_name": "Bash",
        "tool_input": {"command": command},
        "cwd": str(cwd),
        "permission_mode": "default",
    }
    verdicts, errors = {}, {}
    for name, guard in GUARDS:
        try:
            verdict = guard(data)
        except Exception as error:  # noqa: BLE001 - a guard crash is a finding, not a stop
            errors[name] = type(error).__name__
            continue
        if verdict:
            verdicts[name] = verdict.decision
    decisions = verdicts.values()
    decision = (
        "deny" if "deny" in decisions else "ask" if "ask" in decisions else "defer"
    )
    routed = decision == "defer" and bash_risk_judge.should_judge(
        command, parse(command, str(cwd), shell_sources.resolve)
    )
    return {
        "decision": decision,
        "verdicts": verdicts,
        "model_candidate": routed,
        "errors": errors,
    }


def summarize(results: list[dict]) -> dict:
    summary = {}
    for label in LABELS:
        selected = [row for row in results if row["upstream"].split()[0] == label]
        summary[label] = {
            "total": len(selected),
            "decisions": dict(collections.Counter(row["decision"] for row in selected)),
            "deferred_model_candidates": sum(
                row["model_candidate"] for row in selected
            ),
            "errors": sum(bool(row["errors"]) for row in selected),
        }
    return summary


def main() -> None:
    args = parse_args()
    rows = load_rows(args.upstream)
    cwd = args.out / "corpus-work"
    cwd.mkdir(parents=True, exist_ok=True)
    os.environ["HOOKS_LOG_DIR"] = str(args.out / "corpus-logs")

    results = []
    with (args.out / "cc-safety-net-replay.jsonl").open("w") as output:
        for index, row in enumerate(rows):
            result = {
                "index": index,
                "upstream": row[LABEL_COLUMN],
                **decide(row["literal"], cwd),
            }
            results.append(result)
            output.write(json.dumps(result) + "\n")

    summary = summarize(results)
    (args.out / "cc-safety-net-replay-summary.json").write_text(
        json.dumps(summary, indent=2)
    )
    print(json.dumps(summary))


if __name__ == "__main__":
    main()
