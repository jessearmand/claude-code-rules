"""Tests for shell_glob: bash pattern matching. Run: python3 shell_glob_test.py"""

from __future__ import annotations

import sys

import shell_glob

# (text, pattern, matches), checked against bash 5.2 (extglob off).
CASES = [
    ("M", "[[:upper:]]", True), ("m", "[[:upper:]]", False), ("7", "[[:digit:]]", True),
    ("a", "[!a]", False), ("b", "[^a]", True), ("x", "[a-z]", True), ("X", "[a-z]", False),
    ("]", "[]]", True), ("-", "[a-]", True), ("a*", "a\\*", True), ("ab", "a\\*", False),
    ("abc", "a*", True), ("a", "?", True), ("", "?", False), ("[", "[", True), ("a.b", "a.b", True),
    ("axb", "a.b", False), ("+(x)", "+(x)", True), ("x", "+(x)", False), ("a\nb", "a?b", True),
]
# With `shopt -s extglob` (bash manual, Pattern Matching).
EXTGLOB_CASES = [
    ("S", "+([[:upper:]])", True), ("SS", "+([[:upper:]])", True), ("", "+(x)", False), ("", "*(x)", True),
    ("b", "@(b|c)", True), ("bc", "@(b|c)", False), ("a", "?(x)a", True), ("xa", "?(x)a", True),
    ("ab", "a@(b|c@(d|e))", True), ("ace", "a@(b|c@(d|e))", True), ("ace", "a@(b|c(d|e))", False), ("+(x)", "+(x)", False),
]


def main() -> int:
    failures = 0
    for text, pattern, expected in CASES:
        got = shell_glob.match(text, pattern)
        ok = got == expected
        failures += not ok
        print(f"  {'pass' if ok else 'FAIL'}  {text!r} ~ {pattern!r} -> {got}")
    with shell_glob.extglob(True):
        for text, pattern, expected in EXTGLOB_CASES:
            got = shell_glob.match(text, pattern)
            ok = got == expected
            failures += not ok
            print(f"  {'pass' if ok else 'FAIL'}  extglob {text!r} ~ {pattern!r} -> {got}")
        try:
            shell_glob.match("a", "!(b)")
            failures += 1
            print("  FAIL  extglob !( ) must raise Unsupported")
        except shell_glob.Unsupported:
            print("  pass  extglob !( ) raises Unsupported")
    print(f"\n{failures} failure(s)" if failures else "\nall passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
