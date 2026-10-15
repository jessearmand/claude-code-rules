"""Tests for shell_arith: bash integer arithmetic. Run: python3 shell_arith_test.py"""

from __future__ import annotations

import sys

import shell_arith

# (expression, expected value; None when not known), checked against bash 5.2.
CASES = [
    ("1&1", 1), ("1|2", 3), ("2^3", 1), ("1<<4", 16), ("-16>>2", -4), ("~-3", 2), ("!0", 1), ("!5", 0),
    ("2**10", 1024), ("1?2:3", 2), ("0?2:3", 3), ("1,2", 2), ("(1+2)*3", 9), ("7/-2", -3), ("-7%3", -1),
    ("0x1f", 31), ("010", 8), ("2#101", 5), ("36#z", 35), ("64#_", 63), ("3>2&&2>3", 0), ("0||4", 1),
    ("i+1", 2), ("$i*${n}", 3), ("j", 2), ("e+1", 1), ("$((i+1))", 2),
    ("9223372036854775807+1", -9223372036854775808),
    ("x+1", None), ("i++", None), ("i=2", None), ("1/0", None), ("1+", None), ("2**-1", None),
]
VARIABLES = {"i": "1", "n": "3", "j": "i+1", "e": ""}


def main() -> int:
    failures = 0
    for expression, expected in CASES:
        got = shell_arith.evaluate(expression, VARIABLES.get)
        ok = got == expected
        failures += not ok
        print(f"  {'pass' if ok else 'FAIL'}  {expression} -> {got} (want {expected})")
    print(f"\n{failures} failure(s)" if failures else "\nall passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
