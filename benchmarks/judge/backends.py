"""Judges that answer a Case: Jev through the hook's own client, decider over HTTP.

Every backend returns a Result with answers in one normalised shape, the one
`ai evaluate` prints, so metrics do not care which model produced them:
  boolean -> {"probability": p}
  choice  -> {"choice": name, "probabilities": {name: p}}
  score   -> {"score": s}
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from corpus import HOOKS_DIR, Case

sys.path.insert(0, HOOKS_DIR)
import jev_client
from jev_client import Question


@dataclass
class Result:
    backend: str
    case: Case
    answers: dict = field(default_factory=dict)
    latency_ms: float | None = None
    error: str | None = None
    usage: dict | None = None

    def to_record(self) -> dict:
        record = {"backend": self.backend, **self.case.to_record(), "latency_ms": self.latency_ms}
        if self.error:
            record["error"] = self.error
        else:
            record["answers"] = self.answers
        if self.usage:
            record["usage"] = self.usage
        if self.case.reference:
            record["reference"] = self.case.reference
        return record


class Backend:
    name = "base"

    def evaluate(self, case: Case) -> Result:
        raise NotImplementedError


class JevBackend(Backend):
    """Exactly the path the hook takes: `ai evaluate` (via fnox) through the AI Gateway."""

    name = "jev"

    def __init__(self) -> None:
        os.environ.setdefault("HOOK_JEV_ENABLE", "1")
        os.environ.setdefault("HOOK_JEV_TIMEOUT", "30")

    def evaluate(self, case: Case) -> Result:
        started = time.perf_counter()
        evaluation = jev_client.evaluate(case.state, case.questions)
        elapsed = (time.perf_counter() - started) * 1000
        if evaluation is None:
            return Result(self.name, case, latency_ms=elapsed, error=jev_client.last_error() or "no answer")
        answers = {
            qid: {k: v for k, v in a.items() if k in ("probability", "choice", "probabilities", "score")}
            for qid, a in evaluation.answers.items()
        }
        return Result(self.name, case, answers, elapsed, usage=evaluation.raw.get("usage"))


def to_systemone_question(question: Question) -> dict:
    """The TypeSafe wire format decider serves; `ai evaluate` builds the same objects (boolean is decider's noul)."""
    if question.kind == "boolean":
        return {"type": "noul", "instructions": question.text}
    if question.kind == "choice":
        return {"type": "choice", "instructions": question.text, "criteria": {o: o for o in question.options}}
    if question.kind == "score":
        return {"type": "score", "instructions": question.text, "criteria": list(question.options)}
    raise ValueError(f"unknown question kind: {question.kind}")


def from_systemone_answer(answer: dict) -> dict:
    if "noul" in answer:
        return {"probability": answer["noul"]}
    if "choice" in answer:
        return {"choice": answer["choice"], "probabilities": answer.get("probabilities", {})}
    if "score" in answer:
        return {"score": answer["score"]}
    return {}


class DeciderBackend(Backend):
    """POST /v1/systemone: a decider server (CUDA, or the MPS shim in this folder) or llama-server with a decision model."""

    name = "decider"

    def __init__(self, url: str = "http://127.0.0.1:8000", timeout: float = 120.0) -> None:
        self.url = url.rstrip("/") + "/v1/systemone"
        self.timeout = timeout

    def evaluate(self, case: Case) -> Result:
        body = json.dumps(
            {"state": case.state, "questions": {q.id: to_systemone_question(q) for q in case.questions}}
        ).encode()
        request = urllib.request.Request(self.url, data=body, headers={"content-type": "application/json"})
        started = time.perf_counter()
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                data = json.loads(response.read())
        except urllib.error.HTTPError as error:
            return Result(
                self.name,
                case,
                latency_ms=(time.perf_counter() - started) * 1000,
                error=f"HTTP {error.code}: {error.read()[:200]!r}",
            )
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            return Result(self.name, case, latency_ms=(time.perf_counter() - started) * 1000, error=str(error))
        elapsed = (time.perf_counter() - started) * 1000
        answers = {qid: from_systemone_answer(a) for qid, a in data.get("answers", {}).items()}
        return Result(self.name, case, answers, elapsed, usage=data.get("usage"))


def make_backend(name: str, url: str | None = None) -> Backend:
    if name == "jev":
        return JevBackend()
    if name in ("decider", "systemone"):
        return DeciderBackend(url or "http://127.0.0.1:8000")
    raise ValueError(f"unknown backend {name!r}; semif results are imported with semif_bridge.py")
