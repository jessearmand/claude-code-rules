"""
Redaction of credentials from text sent to the model judge.

Everything bash_risk_judge.py sends to a third-party evaluator (the command,
the user's prompt, recent commands, script bodies) passes through `redact`
first. Redaction is what makes it acceptable to send that text at all, so it
stays deterministic and errs toward removing too much.

Only literals are redacted. A value that starts with `$`, a backtick, `<` or
`(` is a reference or substitution: it holds no secret itself, and removing it
hides exactly the behaviour the model is being asked about.

The secrets_guard hook remains the real gate on credential access; this module
only keeps credentials that already appear in the text from leaving the machine.
"""

from __future__ import annotations

import re
from collections.abc import Iterator

REDACTED = "<redacted>"

# A credential term, possibly inside a longer name (`AWS_SECRET_ACCESS_KEY`,
# `GITHUB_PAT`, `SLACK_WEBHOOK_URL`), but not one naming where the secret lives
# (`PASSWORD_FILE`, `SECRET_NAME`). `pat` must not start inside a word (`expat`)
# or a file extension (`brush.pat`); `PATH` and `PATTERN` fail on the next letter.
_SECRET_KEY = (
    r"(?:token|secret|password|passwd|pwd|passphrase|api[_-]?key|authorization|bearer|cookie|"
    r"(?<![A-Za-z.])pat|credentials?|webhook|"
    r"(?:private|access|signing|license|encryption|master|session|ssh|gpg|deploy|service)[_-]?key)"
    r"(?:[_-][A-Za-z0-9]+)*(?<![_-]file)(?<![_-]path)(?<![_-]dir)(?<![_-]name)"
)
# HTTP auth schemes are kept so the model still sees what kind of credential it is.
_AUTH_SCHEME = r"(?:bearer|basic|token|digest|negotiate)"
# What may start a value that is a reference, not a literal.
_REFERENCE = r"[$`<(]"

_SECRET_VALUE = re.compile(
    # The key bare (`TOKEN=`) or quoted on both sides (`"api_key":`, `'github_token':`).
    rf"(?i)(?P<key>(?:[\"'][\w-]*?{_SECRET_KEY}[\"']|{_SECRET_KEY})"
    # An auth scheme is kept, quoted or not: `"authorization":"Basic <redacted>"`.
    rf"(?:[\s:=]+[\"']?{_AUTH_SCHEME}\b)?[\s:=]+)(?![\s:=]|[\"']?(?:{_REFERENCE}|{_AUTH_SCHEME}\b|{REDACTED}))"
    # A quoted value is redacted through its closing quote (spaces included).
    # Backslash escapes inside it (`"a\\"b"`) do not end it; nor does `\ ` in a bare value.
    r"(?:\"(?P<double>(?:[^\"\\]|\\.)*)\"|'(?P<single>(?:[^'\\]|\\.)*)'|[\"']?(?:\\.|[^\s\"'\\])+)"
    r"|\b(?:sk|ghp|gho|ghs|ghu|ghr|github_pat|xox[abpe]|xapp|AKIA|AIza|hf|npm_|pypi-|glpat-|dop_v1_)"
    r"[A-Za-z0-9_\-]{16,}"
    r"|\beyJ[A-Za-z0-9_\-]{20,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}"
)

# Inside one shell-quoted word, a credential whose value is not itself quoted:
# `-H 'X-Api-Key: correct horse'`, `"Authorization: Token a b"`, `'password=a b&x=1'`.
# The value runs to the end of the quoted word, not to the first space. Quoted
# words nested inside it are redacted first, so their result is not swallowed.
_SECRET_IN_QUOTED_WORD = re.compile(
    rf"(?is)(?P<key>{_SECRET_KEY}\s*[:=][\s:=]*(?:{_AUTH_SCHEME}\s+)?)(?![\s:=\"']|{_REFERENCE}|{REDACTED}|{_AUTH_SCHEME}\b).+"
)

# Prose labels have no shell-word boundary: a passphrase may contain spaces.
# Consume the rest of that line rather than leaking all but its first word.
# Shell assignments (TOKEN=value cmd) retain the existing token-aware handling.
_PROSE_SECRET_VALUE = re.compile(
    rf"(?im)(?P<key>(?<![\w=.-])[\w-]*?{_SECRET_KEY}(?:[ \t]*:[ \t]*|[ \t]+=[ \t]*))"
    rf"(?![ \t\"']|{_REFERENCE}|{REDACTED}|{_AUTH_SCHEME}\b)[^\r\n]+"
)

# Incoming-webhook URLs: the path is the credential (PR review).
_WEBHOOK_URL = re.compile(
    r"(https?://(?:hooks\.slack\.com/services/|discord(?:app)?\.com/api/webhooks/|"
    r"[\w.-]*webhook\.office\.com/webhookb2/|api\.telegram\.org/bot|hooks\.zapier\.com/hooks/catch/|"
    r"events\.pagerduty\.com/))[^\s\"'<>]+"
)
# `https://user:pass@host`: the password in a URL's userinfo.
_URL_USERINFO = re.compile(r"(\b[a-zA-Z][\w+.-]*://[^\s/:@]+:)[^\s/@]+@")
# The password half of Basic-auth credentials given to curl's -u / --user (spaced,
# attached or with =), -U / --proxy-user, and httpie's -a / --auth.
_BASIC_AUTH_OPTION = re.compile(
    r"((?:\s-u|\s--user|\s--proxy-user|\s-U|\s--auth|\s-a)(?:\s+|=)?)"
    # A quoted credential is redacted through its closing quote (spaces included).
    # Inside double quotes a backslash escapes the next character (`\\"` does not end it).
    r"(?:'([^':]+:)[^']*'|\"((?:[^\"\\:]|\\.)+:)(?:[^\"\\]|\\.)*\"|([^\s:\"']+:)[^\s\"']+)")
# A PEM/OpenSSH/PGP private key block, or one cut off before its END line.
_PRIVATE_KEY = re.compile(
    r"-----BEGIN ([A-Z0-9 ]*PRIVATE KEY(?: BLOCK)?)-----.*?(?:-----END \1-----|\Z)", re.DOTALL)


def quoted_spans(text: str) -> Iterator[tuple[int, int]]:
    """(start, end) of each top-level shell-quoted region's contents, quotes excluded.

    Follows bash quoting: a backslash outside quotes escapes the next character
    (so `it\\'s` opens nothing), `'…'` has no escapes, `$'…'` and `"…"` do.
    An unterminated quote runs to the end of the text.
    """
    i, n = 0, len(text)
    while i < n:
        char = text[i]
        if char == "\\":
            i += 2
            continue
        if char not in "'\"":
            i += 1
            continue
        escapes = char == '"' or (i > 0 and text[i - 1] == "$")
        start = j = i + 1
        while j < n and text[j] != char:
            j += 2 if escapes and text[j] == "\\" else 1
        end = min(j, n)
        yield start, end
        i = end + 1


def _redact_quoted_words(text: str) -> str:
    pieces: list[str] = []
    last = 0
    for start, end in quoted_spans(text):
        pieces.append(text[last:start])
        inner = _redact_quoted_words(text[start:end])
        pieces.append(_SECRET_IN_QUOTED_WORD.sub(lambda m: m.group("key") + REDACTED, inner, count=1))
        last = end
    pieces.append(text[last:])
    return "".join(pieces)


def _basic_auth_redacted(match: re.Match[str]) -> str:
    """`-u 'user:pass words'` -> `-u 'user:<redacted>'`, keeping the quoting."""
    if match.group(2) is not None:
        return f"{match.group(1)}'{match.group(2)}{REDACTED}'"
    if match.group(3) is not None:
        return f'{match.group(1)}"{match.group(3)}{REDACTED}"'
    return f"{match.group(1)}{match.group(4)}{REDACTED}"


def _secret_value_redacted(match: re.Match[str]) -> str:
    key = match.group("key")
    if not key:
        return REDACTED
    quote = '"' if match.group("double") is not None else "'" if match.group("single") is not None else ""
    if not quote and match.group(0)[len(key):][:1] in "\"'":
        quote = match.group(0)[len(key)]  # an unclosed quote stays in place
        return f"{key}{quote}{REDACTED}"
    return f"{key}{quote}{REDACTED}{quote}"


def redact(text: str) -> str:
    """`text` with every recognisable credential literal replaced by `<redacted>`."""
    text = _PRIVATE_KEY.sub("<redacted private key>", text)
    text = _URL_USERINFO.sub(rf"\1{REDACTED}@", text)
    text = _WEBHOOK_URL.sub(rf"\1{REDACTED}", text)
    text = _BASIC_AUTH_OPTION.sub(_basic_auth_redacted, text)
    text = _redact_quoted_words(text)
    text = _PROSE_SECRET_VALUE.sub(lambda m: m.group("key") + REDACTED, text)
    return _SECRET_VALUE.sub(_secret_value_redacted, text)
