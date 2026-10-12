#!/usr/bin/env python3
"""
Thin client for `ai decide` (vercel-labs/ai-cli) backed by a decision model on
Vercel AI Gateway, for use inside PreToolUse hooks. The default is TypeSafe's
Jev; any gateway decision model works (`ai models --type evaluation`), such as
OpenAI's `openai/gpt-6-luna-decisions`.

A decision model answers typed questions (boolean / choice / score) about a piece of shared
state and returns calibrated probabilities instead of prose. That makes it a
good fit for hooks: a hook can ask "is this command destructive?" and get a
number back in well under the hook timeout, with no output to parse beyond JSON.

The contract of this module is that it NEVER raises and NEVER blocks a hook:
every failure mode returns `None`, and the calling hook must treat `None` as
"no opinion" and fall back to its existing deterministic behaviour.

Failure modes that return `None` (all logged only when HOOK_JEV_DEBUG=1):
  * HOOK_JEV_ENABLE is not set to a truthy value  (opt-in; default off)
  * `ai` is not on PATH, or is a version without the `decide` subcommand
  * no gateway credential is in the environment and `fnox` cannot supply one
  * the request times out, exits non-zero, or prints something that is not
    the documented JSON shape

Credentials: `ai` needs AI_GATEWAY_API_KEY (or a VERCEL_OIDC_TOKEN). If either
is already in the hook's environment the CLI is invoked directly. Otherwise the
call is wrapped in `fnox exec`, pointed at HOOK_JEV_FNOX_CONFIG when set, or at
fnox's own search from the hook's working directory when not.

Behind an HTTPS proxy (a corporate proxy, or a sandbox whose proxy injects the
gateway key, where AI_GATEWAY_API_KEY can be any placeholder) Node's built-in
fetch ignores HTTPS_PROXY. When a proxy variable is set, the CLI is run with
NODE_USE_ENV_PROXY=1 (Node >= 22.21) unless that is already set.

Configuration (environment variables):
  HOOK_JEV_ENABLE=1                 turn Jev evaluation on
  HOOK_JEV_MODEL=typesafe-ai/jev    decision model passed as `-m`, e.g.
                                    openai/gpt-6-luna-decisions
  HOOK_JEV_TIMEOUT=5                seconds for the whole `ai decide` call
  HOOK_JEV_FNOX_CONFIG=<path>       fnox.toml to use when the key is not in env
  HOOK_JEV_DEBUG=1                  explain fallbacks on stderr
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
from dataclasses import dataclass, field

DEFAULT_MODEL = "typesafe-ai/jev"
DEFAULT_TIMEOUT_SECONDS = 5.0
API_KEY_ENV = "AI_GATEWAY_API_KEY"
# The gateway also accepts a Vercel OIDC token in place of an API key.
CREDENTIAL_ENVS = (API_KEY_ENV, "VERCEL_OIDC_TOKEN")
_PROXY_ENVS = ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy")


@dataclass(frozen=True)
class Question:
    """One typed question. `kind` is boolean, choice or score."""

    id: str
    kind: str
    text: str
    options: tuple[str, ...] = ()

    def cli_args(self) -> list[str]:
        spec = f"{self.id}={self.text}"
        if self.kind == "boolean":
            return ["--boolean", spec]
        if self.kind == "choice":
            return ["--choice", spec, "--choices", f"{self.id}={','.join(self.options)}"]
        if self.kind == "score":
            return ["--score", spec, "--levels", f"{self.id}={','.join(self.options)}"]
        raise ValueError(f"unknown question kind: {self.kind}")


@dataclass
class Evaluation:
    """Parsed answers keyed by question id, plus the raw result for logging."""

    answers: dict[str, dict] = field(default_factory=dict)
    raw: dict = field(default_factory=dict)

    def probability(self, question_id: str) -> float | None:
        value = self.answers.get(question_id, {}).get("probability")
        return float(value) if isinstance(value, (int, float)) else None

    def choice(self, question_id: str) -> str | None:
        value = self.answers.get(question_id, {}).get("choice")
        return value if isinstance(value, str) else None

    def score(self, question_id: str) -> float | None:
        value = self.answers.get(question_id, {}).get("score")
        return float(value) if isinstance(value, (int, float)) else None

    def confidence(self) -> dict | None:
        """Per-question confidence for choice/score answers, from whichever provider answered."""
        metadata = self.raw.get("providerMetadata")
        if not isinstance(metadata, dict):
            return None
        for provider, values in metadata.items():
            if provider != "gateway" and isinstance(values, dict) and isinstance(values.get("confidence"), dict):
                return values["confidence"]
        return None


def enabled() -> bool:
    return os.environ.get("HOOK_JEV_ENABLE", "").strip().lower() in ("1", "true", "yes", "on")


_state = threading.local()


def last_error() -> str | None:
    """Why the most recent evaluate() on this thread fell back, or None."""
    return getattr(_state, "error", None)


def _debug(message: str) -> None:
    """Record why the call fell back; print it only when HOOK_JEV_DEBUG is set."""
    _state.error = message
    if os.environ.get("HOOK_JEV_DEBUG", "").strip().lower() in ("1", "true", "yes", "on"):
        print(f"jev-client: {message}", file=sys.stderr)


def _timeout_seconds() -> float:
    try:
        return max(1.0, float(os.environ.get("HOOK_JEV_TIMEOUT", DEFAULT_TIMEOUT_SECONDS)))
    except ValueError:
        return DEFAULT_TIMEOUT_SECONDS


def build_command(questions: list[Question], timeout_seconds: float) -> list[str] | None:
    """Resolve the `ai decide ...` argv, wrapped in `fnox exec` when needed."""
    ai = shutil.which("ai")
    if not ai:
        _debug("`ai` not found on PATH")
        return None

    # `ai decide` enforces its own deadline including retries; keep it inside
    # ours so the subprocess timeout is the backstop, not the norm.
    cli_timeout = max(1, int(timeout_seconds))
    command = [
        ai,
        "decide",
        "-m",
        os.environ.get("HOOK_JEV_MODEL") or DEFAULT_MODEL,
        "--input",
        "text",
        "--max-retries",
        "0",
        "--timeout",
        str(cli_timeout),
    ]
    for question in questions:
        command += question.cli_args()

    if any(os.environ.get(name) for name in CREDENTIAL_ENVS):
        return command

    fnox = shutil.which("fnox")
    if not fnox:
        _debug(f"{API_KEY_ENV} not set and `fnox` not found on PATH")
        return None
    wrapper = [fnox]
    config = os.environ.get("HOOK_JEV_FNOX_CONFIG")
    if config:
        wrapper += ["-c", os.path.expanduser(config)]
    wrapper += ["exec", "--if-missing", "error", "--"]
    return wrapper + command


def _subprocess_env() -> dict[str, str] | None:
    """The CLI's environment: inherited, plus Node's env-proxy switch when a proxy is set."""
    if os.environ.get("NODE_USE_ENV_PROXY") or not any(os.environ.get(name) for name in _PROXY_ENVS):
        return None
    # The switch makes Node print an "experimental" warning on every call.
    return {**os.environ, "NODE_USE_ENV_PROXY": "1", "NODE_NO_WARNINGS": "1"}


def _is_probability(value: object) -> bool:
    # bool is an int subclass; `true` must not be read as P=1.0.
    return isinstance(value, (int, float)) and not isinstance(value, bool) and 0.0 <= value <= 1.0


def _answer_problem(answer: object) -> str | None:
    """Why an answer cannot be trusted, or None. One bad answer voids the whole result."""
    if not isinstance(answer, dict):
        return "not an object"
    if "probability" in answer and not _is_probability(answer["probability"]):
        return f"probability {answer['probability']!r} is not a number in [0, 1]"
    if "choice" in answer and not isinstance(answer["choice"], str):
        return f"choice {answer['choice']!r} is not a string"
    if "score" in answer and (isinstance(answer["score"], bool) or not isinstance(answer["score"], (int, float))):
        return f"score {answer['score']!r} is not a number"
    return None


def _parse(stdout: str) -> Evaluation | None:
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        _debug("stdout was not JSON")
        return None
    answers = data.get("answers") if isinstance(data, dict) else None
    if not isinstance(answers, dict):
        _debug("JSON had no `answers` object")
        return None
    for question_id, answer in answers.items():
        problem = _answer_problem(answer)
        if problem:
            _debug(f"answer {question_id!r} rejected: {problem}")
            return None
    return Evaluation(answers=answers, raw=data)


def evaluate(state: str, questions: list[Question]) -> Evaluation | None:
    """Ask the decision model `questions` about `state`. Returns None whenever it cannot answer."""
    _state.error = None
    if not enabled():
        return None
    if not state.strip() or not questions:
        return None

    timeout_seconds = _timeout_seconds()
    command = build_command(questions, timeout_seconds)
    if command is None:
        return None

    try:
        completed = subprocess.run(
            command,
            input=state,
            capture_output=True,
            text=True,
            timeout=timeout_seconds + 1.0,
            check=False,
            env=_subprocess_env(),
        )
    except subprocess.TimeoutExpired:
        _debug(f"`ai decide` did not complete: timed out after {timeout_seconds + 1.0:g}s")
        return None
    except OSError as error:
        _debug(f"`ai decide` did not complete: {error}")
        return None

    if completed.returncode != 0:
        # The cause is the CLI's last line; anything before it is Node noise.
        _debug(f"`ai decide` exited {completed.returncode}: {completed.stderr.strip()[-200:]}")
        return None
    return _parse(completed.stdout)
