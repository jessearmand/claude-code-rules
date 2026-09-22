"""The fixed corpus every judge is scored on.

Two sources:
  * the labelled cases jev_calibrate.py already uses (rm_rf_guard, secrets_guard,
    file_protection test corpora), each with a deterministic expected answer;
  * the live hook log (~/.claude/hooks-logs), commands that reached Jev in real
    sessions, kept only when the logged command is not truncated. These carry
    Jev's recorded answers instead of a label.
"""

from __future__ import annotations

import glob
import json
import os
import sys
from dataclasses import dataclass, field

HOOKS_DIR = os.path.join(os.path.dirname(os.path.realpath(__file__)), "..", "..", "hooks")
sys.path.insert(0, os.path.normpath(HOOKS_DIR))

from bash_risk_judge import QUESTIONS as HOOK_QUESTIONS
from bash_risk_judge import build_state
from jev_calibrate import COMMAND_QUESTIONS, CWD, PATH_QUESTIONS
from jev_calibrate import corpus as labelled_corpus
from jev_client import Question

DEFAULT_LOG_DIR = os.environ.get("JUDGE_LOG_DIR") or os.path.expanduser("~/.claude/hooks-logs")
# The audit log grows while a benchmark runs (the benchmark's own shell commands go through the hook), so the
# live corpus is frozen at a timestamp: entries logged after it are ignored. ISO 8601, UTC, as the log writes it.
DEFAULT_LOG_UNTIL = os.environ.get("JUDGE_LOG_UNTIL", "2026-09-22T15:45:00+00:00")
LIVE_QUESTION_IDS = tuple(q.id for q in HOOK_QUESTIONS)


@dataclass
class Case:
    """One state plus the questions asked about it."""

    key: str
    corpus: str
    subject: str
    state: str
    questions: list[Question]
    question_id: str | None = None  # the labelled question, when there is one
    expected: bool | None = None
    reference: dict = field(default_factory=dict)  # answers recorded by another judge (live log)

    def to_record(self) -> dict:
        return {
            "key": self.key,
            "corpus": self.corpus,
            "subject": self.subject,
            "question": self.question_id,
            "expected": self.expected,
        }


def _labelled_cases() -> list[Case]:
    cases = []
    for i, c in enumerate(labelled_corpus()):
        if c.question_id in ("secret_path", "generated_path"):
            state, questions = f"path: {c.subject}\n", PATH_QUESTIONS
        else:
            state, questions = build_state(c.subject, CWD), COMMAND_QUESTIONS
        cases.append(Case(f"L{i:03d}", c.corpus, c.subject, state, list(questions), c.question_id, c.expected))
    return cases


def _normalise_logged(answers: dict) -> dict:
    out = {}
    for qid, answer in answers.items():
        if not isinstance(answer, dict):
            continue
        entry = {}
        if "probability" in answer:
            entry["probability"] = answer["probability"]
        if "choice" in answer:
            entry["choice"] = answer["choice"]
        if "probabilities" in answer:
            entry["probabilities"] = answer["probabilities"]
        out[qid] = entry
    return out


def _live_cases(log_dir: str, log_until: str) -> list[Case]:
    """Commands the hook actually sent to Jev, with Jev's answers as the reference."""
    seen: dict[str, Case] = {}
    for path in sorted(glob.glob(os.path.join(log_dir, "*.jsonl"))):
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if record.get("hook") != "bash-risk-judge" or "answers" not in record:
                    continue
                command = record.get("command", "")
                if not command or command.endswith("..."):
                    continue  # truncated in the audit log; cannot be replayed faithfully
                if str(record.get("ts", "")) > log_until:
                    continue  # logged after the freeze point
                cwd = record.get("cwd") or CWD
                if command in seen:
                    continue
                seen[command] = Case(
                    f"H{len(seen):03d}",
                    "hook_log",
                    command,
                    build_state(command, cwd),
                    list(HOOK_QUESTIONS),
                    reference={"jev_logged": _normalise_logged(record["answers"]), "logged_at": record.get("ts")},
                )
    return list(seen.values())


def load_corpus(
    include_live: bool = True, log_dir: str = DEFAULT_LOG_DIR, log_until: str = DEFAULT_LOG_UNTIL
) -> list[Case]:
    cases = _labelled_cases()
    if include_live:
        cases += _live_cases(log_dir, log_until)
    return cases


if __name__ == "__main__":
    from collections import Counter

    cases = load_corpus()
    print(f"{len(cases)} cases:", dict(Counter(c.corpus for c in cases)))
