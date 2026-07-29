#!/usr/bin/env python3
"""
Rule tables for secrets_guard.py.

Kept separate from the matching logic so the policy can be reviewed and edited
without reading any code:

  SECRET_FILES     credential file paths, for Read / Edit / Write / Grep
  SECRET_COMMANDS  shell commands that read, mutate, or exfiltrate secrets
  ALLOWLIST        templates and examples that are never secrets

Levels gate how much is enforced (see safety_level() in secrets_guard.py):
  critical  SSH keys, cloud credentials, .env files
  high      + secrets files, env dumps, exfiltration attempts   <- default
  strict    + database configs and anything that might hold a secret

Generated files that must not be hand-edited (lockfiles, .git/ internals) are a
separate concern and live in file_protection.py.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

CRITICAL = "critical"
HIGH = "high"
STRICT = "strict"


@dataclass(frozen=True)
class FileRule:
    """A path pattern that should be neither read nor written."""

    rule_id: str
    level: str
    pattern: re.Pattern[str]
    reason: str
    remediation: str | None = None


@dataclass(frozen=True)
class CommandRule:
    """A shell command pattern that would expose or exfiltrate a secret."""

    rule_id: str
    level: str
    pattern: re.Pattern[str]
    reason: str
    remediation: str | None = None


def _rx(pattern: str) -> re.Pattern[str]:
    """Compile a rule pattern.

    MULTILINE matters because secrets_guard normalizes a command into one
    statement per line before matching, so `^`/`$` in a command rule must mean
    "start/end of statement". Paths do not contain newlines, so the flag is a
    no-op for file rules.
    """
    return re.compile(pattern, re.IGNORECASE | re.MULTILINE)


# --------------------------------------------------------------------------- #
# Allowlist: checked before every rule, for both paths and command tokens.
# --------------------------------------------------------------------------- #

ALLOWLIST = _rx(
    r"(?:^|[\w./-]*?)"
    r"(?:\.env\.(?:example|sample|template|schema|defaults|dist)"
    r"|env\.example|example\.env|\.env\.example\.\w+)$"
)

# Same idea, but usable mid-command so `cat .env.example; cat .env` still trips
# the second half instead of the whole command being waved through.
ALLOWLIST_IN_COMMAND = _rx(
    r"\S*(?:\.env\.(?:example|sample|template|schema|defaults|dist)|env\.example|example\.env)\b"
)

# Commands that can only ever mention a secret path as literal text.
EXEMPT_COMMAND_HEADS = _rx(
    r"^(?:git\s+(?:commit|tag|notes|log|show|diff|status|blame)"
    r"|gh\s+(?:pr|issue|release|gist)\s+\w+"
    r"|glab\s+\w+\s+\w+"
    r"|jj\s+(?:describe|commit)"
    r"|echo\s+['\"][^'\"]*['\"]\s*$)\b"
)

# `secrets`/`credentials` must be a whole path component, optionally with a
# config extension — never a fragment of a longer identifier. Without the
# lookbehind, source files such as `protect-secrets.js` or `secrets_guard.py`
# match, and routine work on them gets blocked as if they held credentials.
_SECRET_NAME = r"(?<![\w.-])(?:secrets?|credentials?)(?:\.(?:json|ya?ml|toml|ini))?\b"

# Reusable fragment: a token that names something secret. Used by rules that
# would otherwise fire on entirely innocent pipelines.
_SECRET_TOKEN = (
    rf"(?:\.envrc\b|\.env\b|{_SECRET_NAME}|id_rsa\b|id_ed25519\b|id_ecdsa\b"
    r"|id_dsa\b|\.pem\b|\.key\b|\.netrc\b|authorized_keys\b|\.p12\b|\.pfx\b|\.pgpass\b)"
)
_READERS = r"(?:cat|bat|less|more|head|tail|view|nl|od|xxd|strings|open)"
_EDITORS = r"(?:nano|vi|vim|nvim|emacs|code|codium|subl|atom|gedit|micro|helix|hx)"
_SEG = r"[^;|&\n]*"  # stay inside one simple command

# A command name only counts when it sits in a command position: the start of a
# statement (secrets_guard puts one per line), after a pipe, inside a command
# substitution, or behind a wrapper such as sudo/xargs/find -exec.
#
# Without this, any command carrying prose matches — an MR titled
# "rm -rf guard, and split secrets from ..." looked exactly like `rm <secret>`,
# and every gh/glab/git invocation with a description was a candidate.
_HEAD = (
    r"(?:^|\|\s*|\$\(\s*|`\s*|\(\s*|\{\s*"
    r"|\b(?:sudo|doas|env|command|builtin|exec|time|nohup|nice|ionice|xargs)\s+(?:-\S+\s+)*"
    r"|-exec(?:dir)?\s+)"
)

_ENV_SAFE_HINT = (
    "Do not read the file. If you need to know which keys exist, ask the user to name them, "
    "or have the user run the command themselves by typing `! <command>` in the prompt. "
    "If `env-safe` is installed, `env-safe list` / `env-safe check KEY` show keys without values."
)
_ENV_WRITE_HINT = (
    "Do not write this file. Tell the user exactly which key and value to add, and let them "
    "edit it outside Claude Code."
)


# --------------------------------------------------------------------------- #
# Sensitive files, matched against Read / Edit / Write / Grep paths.
# --------------------------------------------------------------------------- #

SECRET_FILES: tuple[FileRule, ...] = (
    # ---- critical ----
    FileRule("env-file", CRITICAL, _rx(r"(?:^|/)\.env(?:\.[^/]*)?$"), ".env file contains secrets",
             remediation=_ENV_SAFE_HINT),
    FileRule("envrc", CRITICAL, _rx(r"(?:^|/)\.envrc$"), ".envrc (direnv) contains secrets",
             remediation=_ENV_SAFE_HINT),
    FileRule("ssh-private-key", CRITICAL, _rx(r"(?:^|/)\.ssh/id_[^/]+$"), "SSH private key"),
    FileRule("ssh-private-key-bare", CRITICAL, _rx(r"(?:^|/)id_(?:rsa|ed25519|ecdsa|dsa)(?:\.pub)?$"),
             "SSH key material"),
    FileRule("ssh-authorized-keys", CRITICAL, _rx(r"(?:^|/)\.ssh/authorized_keys$"), "SSH authorized_keys"),
    FileRule("aws-credentials", CRITICAL, _rx(r"(?:^|/)\.aws/credentials$"), "AWS credentials file"),
    FileRule("aws-config", CRITICAL, _rx(r"(?:^|/)\.aws/config$"), "AWS config may contain secrets"),
    FileRule("kube-config", CRITICAL, _rx(r"(?:^|/)\.kube/config$"), "Kubernetes config contains credentials"),
    FileRule("pem-key", CRITICAL, _rx(r"\.pem$"), "PEM key file"),
    FileRule("key-file", CRITICAL, _rx(r"\.key$"), "Key file"),
    FileRule("pkcs12-key", CRITICAL, _rx(r"\.(?:p12|pfx)$"), "PKCS#12 key file"),
    FileRule("age-key", CRITICAL, _rx(r"(?:^|/)(?:key\.txt|keys\.txt|age\.key)$"), "age/sops key material"),

    # ---- high ----
    # Tool-specific rules come first so their id appears in the message; the
    # generic secrets-file rule below would otherwise shadow them.
    FileRule("cargo-credentials", HIGH, _rx(r"(?:^|/)\.cargo/credentials(?:\.toml)?$"),
             "crates.io token"),
    FileRule("gem-credentials", HIGH, _rx(r"(?:^|/)\.gem/credentials$"), "RubyGems credentials"),
    FileRule("credentials-json", HIGH, _rx(r"(?:^|/)credentials\.json$"), "Credentials file"),
    FileRule("secrets-file", HIGH, _rx(r"(?:^|/)(?:secrets?|credentials?)\.(?:json|ya?ml|toml|ini)$"),
             "Secrets configuration file"),
    FileRule("service-account", HIGH, _rx(r"service[_-]?account[^/]*\.json$"), "GCP service account key"),
    FileRule("gcloud-creds", HIGH, _rx(r"(?:^|/)\.config/gcloud/.*(?:credentials|tokens)"), "gcloud credentials"),
    FileRule("azure-creds", HIGH, _rx(r"(?:^|/)\.azure/(?:credentials|accessTokens)"), "Azure credentials"),
    FileRule("docker-config", HIGH, _rx(r"(?:^|/)\.docker/config\.json$"), "Docker config holds registry auth"),
    FileRule("netrc", HIGH, _rx(r"(?:^|/)\.netrc$"), ".netrc contains credentials"),
    FileRule("npmrc", HIGH, _rx(r"(?:^|/)\.npmrc$"), ".npmrc may contain auth tokens"),
    FileRule("pypirc", HIGH, _rx(r"(?:^|/)\.pypirc$"), ".pypirc contains PyPI credentials"),
    FileRule("vault-token", HIGH, _rx(r"(?:^|/)\.?vault-token$"), "Vault token file"),
    FileRule("keystore", HIGH, _rx(r"\.(?:keystore|jks)$"), "Java keystore"),
    FileRule("htpasswd", HIGH, _rx(r"(?:^|/)\.?htpasswd$"), "htpasswd contains hashed passwords"),
    FileRule("pgpass", HIGH, _rx(r"(?:^|/)\.pgpass$"), "PostgreSQL password file"),
    FileRule("my-cnf", HIGH, _rx(r"(?:^|/)\.my\.cnf$"), "MySQL config may contain a password"),
    FileRule("git-config-file", HIGH, _rx(r"(?:^|/)\.git/config$"),
             ".git/config can embed tokens in remote URLs"),
    FileRule("terraform-state", HIGH, _rx(r"(?:^|/)terraform\.tfstate(?:\.backup)?$"),
             "Terraform state stores resource secrets in plaintext"),
    FileRule("tfvars", HIGH, _rx(r"\.auto\.tfvars$|(?:^|/)terraform\.tfvars$"),
             "Terraform variable files often hold credentials"),

    # ---- strict ----
    FileRule("database-config", STRICT, _rx(r"(?:^|/)(?:config/)?database\.(?:json|ya?ml)$"),
             "Database config may contain passwords"),
    FileRule("ssh-known-hosts", STRICT, _rx(r"(?:^|/)\.ssh/known_hosts$"),
             "known_hosts reveals infrastructure"),
    FileRule("gitconfig", STRICT, _rx(r"(?:^|/)\.gitconfig$"), ".gitconfig may contain credentials"),
    FileRule("curlrc", STRICT, _rx(r"(?:^|/)\.curlrc$"), ".curlrc may contain auth headers"),
)


# --------------------------------------------------------------------------- #
# Bash commands that expose, modify, or exfiltrate secrets.
# --------------------------------------------------------------------------- #

SECRET_COMMANDS: tuple[CommandRule, ...] = (
    # ---- critical: direct reads ----
    CommandRule("read-env", CRITICAL, _rx(rf"{_HEAD}{_READERS}\s+{_SEG}\.env\b"),
                "Reading a .env file exposes secrets", _ENV_SAFE_HINT),
    CommandRule("read-private-key", CRITICAL,
                _rx(rf"{_HEAD}{_READERS}\s+{_SEG}(?:id_rsa|id_ed25519|id_ecdsa|id_dsa|\.pem|\.key)\b"),
                "Reading private key material"),
    CommandRule("read-cloud-creds", CRITICAL,
                _rx(rf"{_HEAD}{_READERS}\s+{_SEG}(?:\.aws/credentials|\.kube/config|\.azure/)"),
                "Reading cloud credentials"),
    CommandRule("edit-env", CRITICAL, _rx(rf"{_HEAD}{_EDITORS}\s+{_SEG}\.env\b"),
                "Opening a .env file in an editor", _ENV_WRITE_HINT),

    # ---- high: environment exposure ----
    CommandRule("env-dump", HIGH, _rx(r"\bprintenv\b|(?:^|[;|&])\s*env\b\s*(?:$|[;|&])"),
                "Dumping the environment may expose secrets",
                "Check for one specific variable instead, e.g. `test -n \"${KEY:-}\" && echo set`."),
    CommandRule("echo-secret-var", HIGH,
                _rx(rf"{_HEAD}(?:echo|printf|print)\b[^;|&]*\$\{{?[A-Za-z_]*"
                    r"(?:SECRET|APIKEY|API_KEY|KEY|TOKEN|PASSWORD|PASSWD|PASS|CREDENTIAL|AUTH|PRIVATE|SALT)"
                    r"[A-Za-z_]*\}?"),
                "Echoing a secret-looking variable",
                "Test whether it is set without printing it: `[ -n \"${VAR:-}\" ]`."),
    CommandRule("read-secrets-file", HIGH,
                _rx(rf"{_HEAD}{_READERS}\s+{_SEG}(?:credentials?|secrets?)\.(?:json|ya?ml|toml|ini)\b"),
                "Reading a secrets file"),
    CommandRule("read-netrc", HIGH,
                _rx(rf"{_HEAD}{_READERS}\s+{_SEG}(?:\.netrc|\.npmrc|\.pypirc|\.pgpass)\b"),
                "Reading a credentials dotfile"),
    CommandRule("source-env", HIGH,
                _rx(rf"{_HEAD}source\s+{_SEG}\.env\b|(?:^|[;|&]\s*)\.\s+{_SEG}\.env\b"),
                "Sourcing a .env file loads secrets into the shell"),
    CommandRule("proc-environ", HIGH, _rx(r"/proc/[^/\s]*/environ"),
                "Reading a process environment block"),

    # ---- high: search that would print secret contents ----
    CommandRule("grep-env", HIGH, _rx(rf"{_HEAD}(?:grep|rg|ag|ack|sift|ugrep)\b{_SEG}\.env\b"),
                "Grepping a .env file prints matching secret lines",
                _ENV_SAFE_HINT),
    CommandRule("find-env", HIGH, _rx(rf"{_HEAD}find\b{_SEG}-name\s+['\"]?\.env"),
                "Locating .env files as a prelude to reading them",
                "If you only need to know whether one exists, use `test -f .env`."),
    CommandRule("xargs-read-secret", HIGH,
                _rx(rf"{_HEAD}xargs\s+(?:-\S+\s+)*{_READERS}\b{_SEG}{_SECRET_TOKEN}"
                    rf"|{_SECRET_TOKEN}{_SEG}\|{_SEG}xargs\b{_SEG}{_READERS}\b"),
                "Reading secret files through xargs"),
    CommandRule("find-exec-read-secret", HIGH,
                _rx(rf"{_HEAD}find\b{_SEG}{_SECRET_TOKEN}{_SEG}-exec"
                    rf"|{_HEAD}find\b{_SEG}-exec\s+{_READERS}{_SEG}{_SECRET_TOKEN}"),
                "Reading secret files through find -exec"),

    # ---- high: exfiltration ----
    # Requires the secret to be an actual file argument (`@file`, --upload-file,
    # -T, or a redirect). Matching any POST that merely mentions "credentials"
    # blocked ordinary secrets-manager API calls and caught nothing real.
    CommandRule("curl-upload-secret", HIGH,
                _rx(rf"{_HEAD}curl\b{_SEG}(?:@|--upload-file\s+|-T\s+|<\s*)\S*{_SECRET_TOKEN}"),
                "Uploading a secret file via curl"),
    CommandRule("wget-post-secret", HIGH, _rx(rf"{_HEAD}wget\b{_SEG}--post-file{_SEG}{_SECRET_TOKEN}"),
                "POSTing secrets via wget"),
    CommandRule("scp-secret", HIGH, _rx(rf"{_HEAD}scp\b{_SEG}{_SECRET_TOKEN}{_SEG}\S+:"),
                "Copying secrets to a remote host via scp"),
    CommandRule("rsync-secret", HIGH, _rx(rf"{_HEAD}rsync\b{_SEG}{_SECRET_TOKEN}{_SEG}\S+:"),
                "Syncing secrets to a remote host via rsync"),
    CommandRule("nc-secret", HIGH, _rx(rf"{_HEAD}(?:nc|ncat|netcat|socat)\b{_SEG}<{_SEG}{_SECRET_TOKEN}"),
                "Exfiltrating secrets via netcat"),
    CommandRule("pipe-secret-to-clipboard", HIGH,
                _rx(rf"{_SECRET_TOKEN}{_SEG}\|{_SEG}\b(?:pbcopy|xclip|xsel|wl-copy)\b"),
                "Copying secrets to the clipboard"),

    # ---- high: mutating secret files ----
    CommandRule("write-env-redirect", HIGH, _rx(r">>?\s*\S*\.env\b"),
                "Redirecting output into a .env file", _ENV_WRITE_HINT),
    CommandRule("tee-env", HIGH, _rx(rf"{_HEAD}tee\b{_SEG}\S*\.env\b"),
                "Writing a .env file via tee", _ENV_WRITE_HINT),
    CommandRule("sed-inplace-env", HIGH, _rx(rf"{_HEAD}sed\b{_SEG}-i{_SEG}\.env\b"),
                "Editing a .env file in place", _ENV_WRITE_HINT),
    CommandRule("copy-secret", HIGH, _rx(rf"{_HEAD}cp\b{_SEG}{_SECRET_TOKEN}"),
                "Copying a secret file"),
    CommandRule("move-secret", HIGH, _rx(rf"{_HEAD}mv\b{_SEG}{_SECRET_TOKEN}"),
                "Moving a secret file"),
    CommandRule("touch-env", HIGH, _rx(rf"{_HEAD}touch\b{_SEG}\S*\.env\b"),
                "Creating a .env file", _ENV_WRITE_HINT),
    CommandRule("delete-secret", HIGH,
                _rx(rf"{_HEAD}rm\b{_SEG}{_SECRET_TOKEN}|{_HEAD}rm\b{_SEG}authorized_keys"),
                "Deleting a secret file",
                "Losing these is unrecoverable. Confirm with the user first."),
    CommandRule("truncate-secret", HIGH,
                _rx(rf"{_HEAD}truncate\b{_SEG}\.(?:env|pem|key)\b|(?:^|[;|&]\s*)>\s*\S*\.env\b"),
                "Truncating a secret file"),

    # ---- strict ----
    CommandRule("grep-for-secrets", STRICT,
                _rx(rf"{_HEAD}(?:grep|rg)\b[^|;]*(?:-r|-R|--recursive)[^|;]*"
                    r"(?:password|passwd|secret|api.?key|token|credential|private.?key)"),
                "Recursive grep for secrets would print them",
                "Search for the variable *name* only, e.g. `rg -l 'API_KEY'`, and read no values."),
    CommandRule("base64-secret", STRICT, _rx(rf"{_HEAD}base64\b{_SEG}{_SECRET_TOKEN}"),
                "Base64-encoding a secret file"),
    CommandRule("history-grep", STRICT, _rx(rf"{_HEAD}history\b[^|;]*\|[^|;]*(?:grep|rg)\b"),
                "Shell history may contain pasted secrets"),
)


# Header comment above SECRET_FILES still applies: these are matched against the
# path arguments of Read / Edit / MultiEdit / Write / NotebookEdit / Grep / Glob.
