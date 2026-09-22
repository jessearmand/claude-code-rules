"""Move the corpus to SemIf's one-question-per-row JSONL and bring its scores back as a result file.

python3 semif_bridge.py export --out corpus_semif.jsonl
semif-score --backend mlx --mode direct ... --input corpus_semif.jsonl --output semif_raw.jsonl
python3 semif_bridge.py import semif_raw.jsonl --out semif.jsonl
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict

from corpus import Case, load_corpus

BOOLEAN_OPTIONS = [{"id": "yes", "description": "Yes."}, {"id": "no", "description": "No."}]


def export_rows(cases: list[Case]) -> list[dict]:
    rows = []
    for case in cases:
        for question in case.questions:
            if question.kind == "boolean":
                options = BOOLEAN_OPTIONS
            elif question.kind == "choice":
                options = [{"id": o, "description": o} for o in question.options]
            else:
                options = [{"id": str(i), "description": o} for i, o in enumerate(question.options)]
            rows.append(
                {"id": f"{case.key}:{question.id}", "state": case.state, "question": question.text, "options": options}
            )
    return rows


WARMUP_ROWS = 3


def warmup_rows(template: dict) -> list[dict]:
    """Throwaway rows of the same shape as the real ones: SemIf has no warm-up pass, so row 1 of a process pays
    the Metal kernel compile; these are dropped on import."""
    return [{**template, "id": f"warmup-{i}"} for i in range(WARMUP_ROWS)]


def _option_probabilities(row: dict) -> dict[str, float] | None:
    """SemIf direct mode: `option_ids` and `probabilities` are parallel lists (a softmax over the offered options)."""
    ids, probs = row.get("option_ids"), row.get("probabilities")
    if isinstance(ids, list) and isinstance(probs, list) and len(ids) == len(probs):
        return {str(i): float(p) for i, p in zip(ids, probs)}
    if isinstance(probs, dict):
        return {str(k): float(v) for k, v in probs.items()}
    return None


def _latency_ms(row: dict) -> float | None:
    """End-to-end per decision: forward pass plus template rendering and tokenisation (`total_seconds`)."""
    for key in ("total_seconds", "forward_seconds"):
        if isinstance(row.get(key), (int, float)):
            return float(row[key]) * 1000
    return None


def import_rows(raw: list[dict], cases: list[Case], backend: str) -> list[dict]:
    by_key = {case.key: case for case in cases}
    answers: dict[str, dict] = defaultdict(dict)
    latency: dict[str, float] = defaultdict(float)
    errors: dict[str, str] = {}
    for row in raw:
        key, _, qid = str(row.get("id", "")).partition(":")
        if key not in by_key:
            continue
        probs = _option_probabilities(row)
        if probs is None:
            errors[key] = f"no probabilities in row {row.get('id')}: keys {sorted(row)[:12]}"
            continue
        question = next(q for q in by_key[key].questions if q.id == qid)
        if question.kind == "boolean":
            answers[key][qid] = {"probability": probs.get("yes", 0.0)}
        elif question.kind == "choice":
            answers[key][qid] = {"choice": max(probs, key=probs.get), "probabilities": probs}
        else:
            answers[key][qid] = {"score": sum(int(k) * p for k, p in probs.items())}
        ms = _latency_ms(row)
        if ms is not None:
            latency[key] += ms
    records = []
    for key, case in by_key.items():
        record = {"backend": backend, **case.to_record(), "latency_ms": latency.get(key)}
        if key in errors or key not in answers:
            record["error"] = errors.get(key, "no rows scored")
        else:
            record["answers"] = answers[key]
        if case.reference:
            record["reference"] = case.reference
        records.append(record)
    return records


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    exp = sub.add_parser("export")
    exp.add_argument("--out", required=True)
    exp.add_argument("--no-live", action="store_true")
    imp = sub.add_parser("import")
    imp.add_argument("raw")
    imp.add_argument("--out", required=True)
    imp.add_argument("--backend", default="semif")
    imp.add_argument("--no-live", action="store_true")
    args = parser.parse_args()

    cases = load_corpus(include_live=not args.no_live)
    if args.command == "export":
        rows = export_rows(cases)
        rows = warmup_rows(rows[0]) + rows
        with open(args.out, "w", encoding="utf-8") as handle:
            handle.writelines(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)
        print(f"wrote {args.out}: {len(rows)} rows from {len(cases)} cases")
    else:
        with open(args.raw, encoding="utf-8") as handle:
            raw = [json.loads(line) for line in handle if line.strip()]
        records = import_rows(raw, cases, args.backend)
        with open(args.out, "w", encoding="utf-8") as handle:
            handle.writelines(json.dumps(record, ensure_ascii=False) + "\n" for record in records)
        print(f"wrote {args.out}: {len(records)} cases, {sum('error' in r for r in records)} without scores")


if __name__ == "__main__":
    main()
