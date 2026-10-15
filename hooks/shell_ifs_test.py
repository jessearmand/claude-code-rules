#!/usr/bin/env python3
"""Tests for shell_ifs.py. Run: python3 hooks/shell_ifs_test.py

Every expectation below is what bash 5 gives for the same input, both for
word splitting of an unquoted `$X` and for `read` with one to three names.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from shell_ifs import DEFAULT, split

# (text, IFS, fields of an unquoted expansion)
SPLIT_CASES = [
    ("rm -rf  ~", DEFAULT, ["rm", "-rf", "~"]),
    ("  a  ", DEFAULT, ["a"]),
    ("", DEFAULT, []),
    ("rm,-rf,/", ",", ["rm", "-rf", "/"]),
    ("a,,b", ",", ["a", "", "b"]),
    (",a", ",", ["", "a"]),
    ("a,", ",", ["a"]),
    ("a , b", " ,", ["a", "b"]),
    ("a ,, b", " ,", ["a", "", "b"]),
    ("rxm", "x", ["r", "m"]),
    ("a b", "", ["a b"]),
]

# (text, IFS, names, what each name gets)
READ_CASES = [
    ("rxm", "x", 2, ["r", "m"]),
    ("x y z", DEFAULT, 2, ["x", "y z"]),
    ("a,b,", ",", 1, ["a,b,"]),
    ("a,b,", ",", 2, ["a", "b"]),
    ("a,b,,", ",", 2, ["a", "b,,"]),
    ("a,", ",", 1, ["a"]),
    ("a,,", ",", 2, ["a", ""]),
    ("a,b ,", ",", 2, ["a", "b "]),
    ("a,b ,", " ,", 2, ["a", "b"]),
    ("a, b,  ", ",", 2, ["a", " b,  "]),
    ("a,b,c,", ",", 3, ["a", "b", "c"]),
    ("a a", "a", 1, ["a a"]),
]


def main() -> int:
    failures: list[str] = []
    for text, ifs, expected in SPLIT_CASES:
        actual = split(text, ifs)
        ok = actual == expected
        print(f"  {'pass' if ok else 'FAIL'}  split {text!r} by {ifs!r} -> {actual}")
        if not ok:
            failures.append(text)
    for text, ifs, names, expected in READ_CASES:
        actual = split(text, ifs, names)
        actual += [""] * (names - len(actual))
        ok = actual == expected
        print(f"  {'pass' if ok else 'FAIL'}  read {names} from {text!r} by {ifs!r} -> {actual}")
        if not ok:
            failures.append(text)
    print(f"\n{len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
