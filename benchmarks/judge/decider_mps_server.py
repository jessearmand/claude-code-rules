"""A minimal /v1/systemone server for Apple Silicon.

decider.serve (1.1.0) builds EngineV2, which captures CUDA graphs and needs a CUDA
device. On a Mac the supported path is decider.infer.Decider on MPS, so this shim
exposes that object behind the same route and request/response shape, enough for
TypeSafe-format clients and for benchmarks/judge/backends.py. One model, one lock,
no batching across requests.

  .venv312/bin/python benchmarks/judge/decider_mps_server.py Mapika/decider-2b --port 8000
"""

from __future__ import annotations

import argparse
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import ClassVar

BOOLEAN_ALIASES = ("boolean", "bool")


def normalise_questions(questions: dict) -> dict:
    """Accept the `ai evaluate` spelling (`boolean`) as well as TypeSafe's `noul`."""
    out = {}
    for qid, spec in questions.items():
        spec = dict(spec)
        if spec.get("type") in BOOLEAN_ALIASES:
            spec["type"] = "noul"
        out[qid] = spec
    return out


class Handler(BaseHTTPRequestHandler):
    decider: ClassVar = None
    lock: ClassVar = threading.Lock()
    stats: ClassVar[dict] = {"requests": 0, "errors": 0, "model_ms": []}

    def _send(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path == "/health":
            self._send(200, {"ok": self.decider is not None})
        elif self.path == "/stats":
            ms = self.stats["model_ms"]
            self._send(
                200,
                {
                    **{k: v for k, v in self.stats.items() if k != "model_ms"},
                    "model_ms_median": sorted(ms)[len(ms) // 2] if ms else None,
                },
            )
        else:
            self._send(404, {"detail": "not found"})

    def do_POST(self) -> None:
        if self.path != "/v1/systemone":
            return self._send(404, {"detail": "not found"})
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("content-length", 0))))
            state, questions = body["state"], normalise_questions(body["questions"])
            independent = bool(body.get("independent", True))
        except (ValueError, KeyError, TypeError) as error:
            return self._send(422, {"detail": f"bad request: {error}"})
        self.stats["requests"] += 1
        try:
            with self.lock:
                started = time.perf_counter()
                result = self.decider.system_one(state, questions, independent=independent)
                self.stats["model_ms"].append((time.perf_counter() - started) * 1000)
        except ValueError as error:
            self.stats["errors"] += 1
            return self._send(422, {"detail": str(error)})
        except Exception as error:  # noqa: BLE001 - report the failure, keep serving
            self.stats["errors"] += 1
            return self._send(500, {"detail": f"{type(error).__name__}: {error}"})
        self._send(200, result)

    def log_message(self, format: str, *args) -> None:
        pass


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("model", nargs="?", default="Mapika/decider-2b")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--device", help="cuda, mps or cpu; default picks the best available")
    parser.add_argument("--temperature", type=float, help="override the checkpoint's decider_config.json temperature")
    args = parser.parse_args()

    from decider.infer import Decider

    started = time.perf_counter()
    Handler.decider = Decider(args.model, device=args.device, temperature=args.temperature)
    print(
        f"[mps-server] loaded {args.model} as {Handler.decider.name} on {Handler.decider.dev}, "
        f"T={Handler.decider.T}, in {time.perf_counter() - started:.0f}s",
        flush=True,
    )
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"[mps-server] listening on http://{args.host}:{args.port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
