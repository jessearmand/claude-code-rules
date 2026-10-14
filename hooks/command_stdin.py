"""Bounded, provenance-preserving literal stdin for deterministic command rules."""

from __future__ import annotations

import db_client_files
from danger_rule import RuleContext
from shell_expand import command_output
from shell_parse import SimpleCommand


def known_stdin(
    command: SimpleCommand, ctx: RuleContext
) -> db_client_files.ClientInput | None:
    """Literal heredoc/pipe/file input, or None when its contents are unknown."""
    redirects = [
        r
        for r in command.redirects
        if r.fd in (None, 0) and r.op in ("<", "<<", "<<-", "<<<", "<&")
    ]
    if redirects:
        redirect = redirects[-1]
        if redirect.dynamic:
            return None
        if redirect.op in ("<<", "<<-", "<<<"):
            return db_client_files.ClientInput(
                redirect.body if redirect.body is not None else redirect.target,
                not command.indirect,
            )
        if redirect.op == "<":
            return db_client_files.read_file(
                redirect.target, command.possible_cwds, ctx.parsed.written_files
            )
        return None
    feeder = ctx.parsed.previous_stage(command)
    if feeder is None or feeder.dynamic_head:
        return None
    # Output redirection replaces the pipe's producer output; do not infer bytes it won't send.
    if any(
        r.op in (">", ">>", ">|", "&>", "&>>", ">&") and r.fd in (None, 1)
        for r in feeder.redirects
    ):
        return None
    if feeder.name in ("echo", "printf"):
        if any("$" in arg or "`" in arg for arg in feeder.args):
            return None
        text = command_output(feeder)
        return (
            db_client_files.ClientInput(text, not feeder.indirect)
            if text is not None
            else None
        )
    if feeder.name != "cat":
        return None
    if not feeder.args:
        return known_stdin(feeder, ctx)
    if any(a.startswith("-") and a != "-" for a in feeder.args):
        return None
    entries = [
        known_stdin(feeder, ctx)
        if name == "-"
        else db_client_files.read_file(
            name, feeder.possible_cwds, ctx.parsed.written_files
        )
        for name in feeder.args
    ]
    if any(entry is None for entry in entries):
        return None
    return db_client_files.ClientInput(
        "".join(entry.text for entry in entries if entry),
        not feeder.indirect and all(entry.typed for entry in entries if entry),
    )
