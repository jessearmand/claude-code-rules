"""Score the corpus with one judge and write one JSON record per case.

python3 benchmarks/judge/run.py jev --out scratchpad/judge/jev.jsonl --workers 8
python3 benchmarks/judge/run.py decider --url http://127.0.0.1:8000 --out scratchpad/judge/decider.jsonl
python3 benchmarks/judge/report.py scratchpad/judge/*.jsonl
"""

from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor

from backends import make_backend
from corpus import load_corpus


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("backend", choices=("jev", "decider", "systemone"))
    parser.add_argument("--out", required=True)
    parser.add_argument("--url", help="base URL of a /v1/systemone server (decider, llama-server)")
    parser.add_argument("--name", help="label for this judge in the result file (default: the backend name)")
    parser.add_argument("--workers", type=int, default=1, help="concurrent requests (keep 1 for clean latency numbers)")
    parser.add_argument("--warmup", type=int, default=2, help="unrecorded calls before the timed run")
    parser.add_argument("--no-live", action="store_true", help="skip the hook-log corpus")
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()

    cases = load_corpus(include_live=not args.no_live)
    if args.limit:
        cases = cases[: args.limit]
    backend = make_backend(args.backend, args.url)
    if args.name:
        backend.name = args.name

    for case in cases[: args.warmup]:
        backend.evaluate(case)

    print(f"{backend.name}: {len(cases)} cases, {args.workers} worker(s)", file=sys.stderr)
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        results = list(pool.map(backend.evaluate, cases))

    with open(args.out, "w", encoding="utf-8") as handle:
        handle.writelines(json.dumps(result.to_record(), ensure_ascii=False) + "\n" for result in results)
    errors = sum(r.error is not None for r in results)
    print(f"wrote {args.out}: {len(results)} records, {errors} errors", file=sys.stderr)
    return 1 if errors == len(results) else 0


if __name__ == "__main__":
    sys.exit(main())
