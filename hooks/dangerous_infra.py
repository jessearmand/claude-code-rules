"""
Destructive infrastructure, cloud, container and database commands.

These are the families the offline eval showed slipping past every hook
(psql, kubectl, terraform, docker, aws, gcloud, helm). Delete verbs that
remove named resources are denied; broad "delete anything" verbs the rules
cannot scope are asked about.

The terraform/aws/gcloud/az lists follow cc-safety-net's official
rulebooks (MIT, cc-safety-net/rulebooks).
"""

from __future__ import annotations

import re

import db_client_files
from danger_rule import (
    HIGH,
    DangerRule,
    RuleContext,
    has,
    long_flag,
    operands,
    positional_words,
    short_flag,
)
from shell_parse import SimpleCommand, echoed_text

# --------------------------------------------------------------------------- #
# Terraform / OpenTofu
# --------------------------------------------------------------------------- #

_TF_TRUE = re.compile(r"^-{1,2}destroy(?:=(?:1|t|true))?$", re.IGNORECASE)


def _terraform(command: SimpleCommand, ctx: RuleContext) -> str | None:
    if command.name not in ("terraform", "tofu", "terragrunt"):
        return None
    words = [a for a in command.args if not a.startswith("-chdir")]
    positional = [a for a in words if not a.startswith("-")]
    if not positional:
        return None
    verb = positional[0]
    dry_run = any(a.lower().startswith("-dry-run") and not a.lower().endswith(("=false", "=0"))
                  for a in words)
    if verb == "destroy":
        return "terraform destroy"
    if verb == "apply" and any(_TF_TRUE.match(a) for a in words):
        return "terraform apply -destroy"
    if verb == "state" and len(positional) > 1 and positional[1] in ("rm", "push") and not dry_run:
        return f"terraform state {positional[1]}"
    if verb == "workspace" and len(positional) > 1 and positional[1] == "delete":
        return "terraform workspace delete"
    return None


# --------------------------------------------------------------------------- #
# Cloud CLIs
# --------------------------------------------------------------------------- #

_AWS_GLOBAL_WITH_VALUE = frozenset({"--ca-bundle", "--cli-binary-format", "--cli-connect-timeout",
                                    "--cli-error-format", "--cli-read-timeout", "--color",
                                    "--endpoint-url", "--output", "--profile", "--query", "--region"})
_AWS_DENY = frozenset({
    ("s3", "rb"), ("ec2", "terminate-instances"), ("cloudformation", "delete-stack"),
    ("rds", "delete-db-instance"), ("rds", "delete-db-cluster"), ("dynamodb", "delete-table"),
    ("eks", "delete-cluster"), ("kms", "schedule-key-deletion"), ("s3api", "delete-bucket"),
    ("s3api", "delete-objects"), ("iam", "delete-user"), ("iam", "delete-role"),
    ("secretsmanager", "delete-secret"), ("route53", "delete-hosted-zone"),
    ("lambda", "delete-function"), ("ecr", "delete-repository"), ("logs", "delete-log-group"),
})
_DESTRUCTIVE_VERB = re.compile(r"^(?:delete|terminate|destroy|purge|remove|deregister|revoke)(?:-|$)")


def _aws(command: SimpleCommand, ctx: RuleContext) -> str | None:
    if command.name != "aws":
        return None
    words = operands(command.args, _AWS_GLOBAL_WITH_VALUE)
    if len(words) < 2:
        return None
    service, action = words[0], words[1]
    if any(a.startswith("--generate-cli-skeleton") for a in command.args) or has(command.args, "--dry-run"):
        return None
    if service == "s3" and action == "rm":
        return None if has(command.args, "--dryrun") else "aws s3 rm"
    if (service, action) in _AWS_DENY:
        return f"aws {service} {action}"
    return None


def _aws_generic(command: SimpleCommand, ctx: RuleContext) -> str | None:
    if command.name != "aws":
        return None
    words = operands(command.args, _AWS_GLOBAL_WITH_VALUE)
    if len(words) >= 2 and _DESTRUCTIVE_VERB.match(words[1]) and not has(command.args, "--dry-run"):
        return f"aws {words[0]} {words[1]}"
    return None


_GCLOUD_GLOBAL_WITH_VALUE = frozenset({"--access-token-file", "--account", "--billing-project",
                                       "--configuration", "--flags-file", "--flatten", "--format",
                                       "--impersonate-service-account", "--project",
                                       "--trace-token", "--verbosity"})
_GCLOUD_DENY = (
    ("projects", "delete"), ("compute", "instances", "delete"), ("compute", "disks", "delete"),
    ("compute", "snapshots", "delete"), ("storage", "rm"), ("storage", "buckets", "delete"),
    ("sql", "instances", "delete"), ("container", "clusters", "delete"),
    ("kms", "keys", "versions", "destroy"), ("secrets", "versions", "destroy"),
    ("secrets", "delete"), ("run", "services", "delete"), ("functions", "delete"),
)


def _gcloud(command: SimpleCommand, ctx: RuleContext) -> str | None:
    if command.name not in ("gcloud", "gsutil"):
        return None
    words = operands(command.args, _GCLOUD_GLOBAL_WITH_VALUE)
    if command.name == "gsutil":
        if words and words[0] in ("rm", "rb"):
            return f"gsutil {words[0]}"
        return None
    if words and words[0] in ("alpha", "beta", "preview"):
        words = words[1:]
    for path in _GCLOUD_DENY:
        if tuple(words[:len(path)]) == path:
            return "gcloud " + " ".join(path)
    return None


def _gcloud_generic(command: SimpleCommand, ctx: RuleContext) -> str | None:
    if command.name != "gcloud":
        return None
    words = operands(command.args, _GCLOUD_GLOBAL_WITH_VALUE)
    verbs = [w for w in words if _DESTRUCTIVE_VERB.match(w)]
    return f"gcloud ... {verbs[0]}" if verbs else None


_AZ_DENY = (
    ("group", "delete"), ("vm", "delete"), ("aks", "delete"), ("storage", "account", "delete"),
    ("storage", "container", "delete"), ("storage", "blob", "delete"), ("sql", "server", "delete"),
    ("sql", "db", "delete"), ("keyvault", "purge"), ("keyvault", "delete"), ("resource", "delete"),
    ("cosmosdb", "delete"),
)


def _az(command: SimpleCommand, ctx: RuleContext) -> str | None:
    if command.name != "az":
        return None
    words = operands(command.args, frozenset({"-o", "--output", "--query", "--subscription"}))
    for path in _AZ_DENY:
        if tuple(words[:len(path)]) == path:
            return "az " + " ".join(path)
    return None


def _az_generic(command: SimpleCommand, ctx: RuleContext) -> str | None:
    if command.name != "az":
        return None
    words = operands(command.args, frozenset({"-o", "--output", "--query", "--subscription"}))
    verbs = [w for w in words if _DESTRUCTIVE_VERB.match(w)]
    return f"az ... {verbs[0]}" if verbs else None


# --------------------------------------------------------------------------- #
# Kubernetes, Helm, containers
# --------------------------------------------------------------------------- #

_KUBE_WITH_VALUE = frozenset({"-n", "--namespace", "--context", "--kubeconfig", "--cluster",
                              "--user", "-l", "--selector", "-f", "--filename", "-o", "--output",
                              "--grace-period", "--timeout"})
_KUBE_BROAD_KINDS = frozenset({"namespace", "namespaces", "ns", "node", "nodes", "no", "pv",
                               "persistentvolume", "persistentvolumes", "pvc",
                               "persistentvolumeclaim", "persistentvolumeclaims", "crd", "crds",
                               "customresourcedefinition", "customresourcedefinitions", "all",
                               "clusterrole", "clusterrolebinding", "storageclass", "secret",
                               "secrets"})


def _kubectl_delete_broad(command: SimpleCommand, ctx: RuleContext) -> str | None:
    if command.name not in ("kubectl", "oc", "k3s") or "delete" not in command.args:
        return None
    if _kube_dry_run(command):
        return None
    words = operands(command.args, _KUBE_WITH_VALUE)
    after = words[words.index("delete") + 1:] if "delete" in words else []
    kinds = {k for w in after for k in w.lower().split(",")} | {w.split("/")[0].lower() for w in after}
    if long_flag(command.args, "--all", min_prefix=5) or long_flag(command.args, "--all-namespaces") or \
            short_flag(command.args, "A") or kinds & _KUBE_BROAD_KINDS:
        return "kubectl delete (namespace/cluster-wide/--all)"
    return None


def _kube_dry_run(command: SimpleCommand) -> bool:
    return any(a.startswith("--dry-run") and not a.endswith("=none") for a in command.args)


def _kubectl_delete(command: SimpleCommand, ctx: RuleContext) -> str | None:
    if _kube_dry_run(command):
        return None
    if command.name in ("kubectl", "oc") and "delete" in operands(command.args, _KUBE_WITH_VALUE):
        return "kubectl delete"
    words = operands(command.args, _KUBE_WITH_VALUE)
    if command.name == "k3s" and words[:1] == ["kubectl"] and "delete" in words[1:]:
        return "k3s kubectl delete"  # k3s runs its embedded kubectl
    if command.name == "helm" and positional_words(command)[:1] in (["uninstall"], ["delete"], ["del"], ["un"]):
        return "helm uninstall"
    return None


def _docker_prune(command: SimpleCommand, ctx: RuleContext) -> str | None:
    if command.name in ("docker-compose", "podman-compose"):
        return _compose_down_volumes(command.name, command.args)
    if command.name not in ("docker", "podman"):
        return None
    words = positional_words(command)
    if words[:1] == ["compose"]:
        return _compose_down_volumes(f"{command.name} compose", command.args[command.args.index("compose") + 1:])
    if words[:2] == ["system", "prune"] and (short_flag(command.args, "a") or long_flag(command.args, "--all", "--volumes")):
        return f"{command.name} system prune --all/--volumes"
    if words[:1] == ["volume"] and len(words) > 1 and words[1] in ("rm", "prune", "remove"):
        return f"{command.name} volume {words[1]}"
    return None


# Compose options before the subcommand that take a value (`-f prod.yml down`).
_COMPOSE_WITH_VALUE = frozenset({"-f", "--file", "-p", "--project-name", "--project-directory",
                                 "--env-file", "--profile", "--ansi", "--progress", "--parallel"})


def _compose_down_volumes(name: str, args: tuple[str, ...]) -> str | None:
    """`docker compose down -v` / `--volumes` deletes the project's volumes."""
    if operands(args, _COMPOSE_WITH_VALUE)[:1] == ["down"] and (
            short_flag(args, "v") or long_flag(args, "--volumes")):
        return f"{name} down --volumes"
    return None


# --------------------------------------------------------------------------- #
# Databases
# --------------------------------------------------------------------------- #

# A table name: plain, or quoted with "", `` or [] (which may hold spaces), schema-qualified.
_SQL_PART = r'(?:"[^"]*"|`[^`]*`|\[[^\]]*\]|[^\s;."`\[]+)'
_SQL_NAME = rf"{_SQL_PART}(?:\.{_SQL_PART})*"
# What a DELETE removes rows from, before or instead of its FROM: MySQL's
# multi-table `DELETE t1, t2.* FROM …` (PR review), after its modifiers.
_DELETE_MODIFIERS = r"(?:(?:LOW_PRIORITY|QUICK|IGNORE)\s+)*"
_DELETE_TARGETS = rf"{_SQL_NAME}(?:\.\*)?(?:\s*,\s*{_SQL_NAME}(?:\.\*)?)*"
_NO_WHERE = r"(?![^;]*\bWHERE\b)"
_SQL_DESTRUCTIVE = re.compile(
    # A DELETE is whole-table when no WHERE follows before the statement ends
    # (`DELETE FROM t;`, `DELETE FROM t RETURNING id;`, `DELETE u FROM users AS u;`),
    # and so is SQL Server's `DELETE t` without FROM, ending the statement.
    rf"\b(?:DROP\s+(?:DATABASE|SCHEMA|TABLE|USER|ROLE)|TRUNCATE\b"
    rf"|DELETE\s+{_DELETE_MODIFIERS}(?:{_DELETE_TARGETS}\s+)?FROM\s+{_SQL_NAME}{_NO_WHERE}"
    rf"|DELETE\s+(?!(?:FROM|CASCADE|RESTRICT|NO|SET|ON|TO|AS|OR|USING|TOP)\b){_SQL_NAME}\s*(?:;|$)"
    rf"|ALTER\s+TABLE\s+{_SQL_NAME}\s+DROP\b)",
    re.IGNORECASE,
)
_SQL_CLIENTS = {
    "psql": ("-c", "--command"),
    "mysql": ("-e", "--execute"),
    "mariadb": ("-e", "--execute"),
    "sqlite3": (),
    "duckdb": ("-c",),
    "clickhouse-client": ("-q", "--query"),
    "cockroach": ("-e", "--execute"),
    "sqlcmd": ("-Q", "-q"),
}
_NOSQL = re.compile(r"\b(?:dropDatabase|\.drop\(\)|deleteMany\(\s*\{\s*\}\s*\)|FLUSHALL|FLUSHDB)\b",
                    re.IGNORECASE)


def _input_file(path: str, command: SimpleCommand, ctx: RuleContext) -> str | None:
    entry = _command_file(path, command, ctx)
    return entry.text if entry else None


def _command_file(path: str, command: SimpleCommand, ctx: RuleContext) -> db_client_files.ClientInput | None:
    """A file the client opens itself, as written on the line or on disk."""
    return db_client_files.read_file(path, command.possible_cwds, ctx.parsed.written_files)


# sqlite3 options that take a value (so it is not read as the database or SQL).
_SQLITE_WITH_VALUE = frozenset({"-init", "--init", "-cmd", "--cmd", "-separator", "--separator",
                                "-newline", "--newline", "-nullvalue", "--nullvalue", "-vfs", "--vfs",
                                "-maxsize", "--maxsize", "-mmap", "--mmap", "-pagecache", "-lookaside"})


def _sql_texts(command: SimpleCommand, ctx: RuleContext) -> list[str]:
    flags = _SQL_CLIENTS.get(command.name)
    if flags is None:
        if command.name in ("mongosh", "mongo"):
            return [a for i, a in enumerate(command.args) if i and command.args[i - 1] == "--eval"]
        if command.name == "redis-cli":
            return [" ".join(command.args)]
        return []
    texts = db_client_files.option_values(command.args, flags,
                                          attached=command.name in db_client_files.GETOPT_CLIENTS)
    # `psql -f q.sql`, `sqlite3 -init q.sql`, `sqlcmd -i q.sql`: a file of statements,
    # as written on the line or on disk.
    files = [_command_file(v, command, ctx) for v in db_client_files.file_option_values(command.name, command.args)]
    if command.name == "sqlite3":
        # `-cmd SQL` runs a statement before the rest.
        texts += [command.args[i + 1] for i, a in enumerate(command.args[:-1]) if a in ("-cmd", "--cmd")]
        texts += [a for a in operands(command.args, _SQLITE_WITH_VALUE)[1:]]
    texts += [r.body or r.target for r in command.redirects if r.op in ("<<", "<<-", "<<<")]
    # `sqlite3 db < q.sql`: the file, as written earlier on the line or as on disk.
    for redirect in command.redirects:
        if redirect.op == "<" and not redirect.dynamic:
            text = _input_file(redirect.target, command, ctx)
            if text:
                texts.append(text)
    # `printf 'DROP TABLE t;' | sqlite3 db`, `cat <<SQL | psql`: statements piped in.
    feeder = ctx.parsed.previous_stage(command)
    if feeder is not None and not feeder.dynamic_head:
        texts += _piped_texts(feeder, ctx)
    # `.read q.sql`, `\i q.sql`, `source q.sql`: files the statements include (PR review).
    # Only a file the client opened itself has a directory for psql's `\ir`; on stdin it is the cwd's.
    inputs = [db_client_files.ClientInput(text, True) for text in texts] + [f for f in files if f]
    inputs += db_client_files.included(command.name, inputs, command.possible_cwds, ctx.parsed.written_files)
    return [entry.text for entry in inputs]


def _piped_texts(feeder: SimpleCommand, ctx: RuleContext) -> list[str]:
    """What a pipeline stage prints when it is literal: echo/printf text, or
    `cat` of its heredoc, here-string or files."""
    if feeder.name in ("echo", "printf"):
        return [echoed_text(feeder)]
    if feeder.name != "cat":
        return []
    texts = [r.body or r.target for r in feeder.redirects if r.op in ("<<", "<<-", "<<<")]
    texts += [r for r in (_input_file(r.target, feeder, ctx) for r in feeder.redirects
                          if r.op == "<" and not r.dynamic) if r]
    texts += [t for t in (_input_file(a, feeder, ctx) for a in operands(feeder.args) if a != "-") if t]
    return texts


# MySQL `/*! … */` and `/*!80000 … */`, MariaDB `/*M! … */`: comments the server
# executes (when it is at least that version) (PR review).
_EXECUTABLE_COMMENT = re.compile(r"/\*M?!\d*")
# PostgreSQL dollar quoting: `$$` or `$tag$`, where a tag cannot start with a digit (`$1` is a parameter).
_DOLLAR_TAG = re.compile(r"\$(?:[A-Za-z_][A-Za-z0-9_]*)?\$")


def _without_comments(sql: str, backslash_escapes: bool = False, blank_quoted: bool = False) -> str:
    """`sql` with `-- ...` and `/* ... */` comments blanked, outside quoted
    strings and identifiers ('', "", ``, []): in `SELECT '--'; DROP TABLE t`
    the `--` is data. `backslash_escapes`: inside a string `\\'` does not end it,
    as in MySQL; standard SQL (SQLite, PostgreSQL) treats the backslash as text.
    The MySQL reading also ends a line at `#` and keeps the body of an
    executable comment (`/*! DROP TABLE t */`, `/*!50000 …*/`, MariaDB's
    `/*M!100100 …*/`), which the server runs; the standard one quotes
    PostgreSQL's `$tag$ ... $tag$` (in `SELECT $$--$$; DROP TABLE t` the `--` is data).
    `blank_quoted`: quoted text is blanked too, keeping its quotes."""
    out: list[str] = []
    index, quote, executable = 0, "", False
    while index < len(sql):
        char = sql[index]
        if quote:
            if backslash_escapes and char == "\\" and quote in "'\"" and index + 1 < len(sql):
                out.append("  " if blank_quoted else sql[index:index + 2])
                index += 2
                continue
            if char == quote:
                quote = ""
            elif blank_quoted:
                out.append(" ")
                index += 1
                continue
        elif char in "'\"`[":
            quote = "]" if char == "[" else char  # `[name]` quotes an identifier (SQLite, SQL Server)
        elif char == "$" and not backslash_escapes and (tag := _DOLLAR_TAG.match(sql, index)):
            delimiter = tag.group(0)
            close = sql.find(delimiter, tag.end())
            end = len(sql) if close < 0 else close + len(delimiter)  # unterminated: quoted to the end
            if blank_quoted:
                closing = "" if close < 0 else delimiter
                out.append(delimiter + " " * (end - tag.end() - len(closing)) + closing)
            else:
                out.append(sql[index:end])
            index = end
            continue
        elif sql.startswith("--", index) or (char == "#" and backslash_escapes):
            end = sql.find("\n", index)
            index = len(sql) if end < 0 else end
            out.append(" ")
            continue
        elif backslash_escapes and (opening := _EXECUTABLE_COMMENT.match(sql, index)):
            executable = True  # its body is SQL: scanned as such, up to its `*/`
            index = opening.end()
            out.append(" ")
            continue
        elif executable and sql.startswith("*/", index):
            executable = False
            index += 2
            out.append(" ")
            continue
        elif sql.startswith("/*", index):
            end = sql.find("*/", index + 2)
            index = len(sql) if end < 0 else end + 2
            out.append(" ")
            continue
        out.append(char)
        index += 1
    return "".join(out)


def _destructive_sql(command: SimpleCommand, ctx: RuleContext) -> str | None:
    for text in _sql_texts(command, ctx):
        # `DELETE FROM users -- all rows` still deletes every row: match without comments,
        # reading backslashes both as MySQL does and as standard SQL does.
        match = None
        for backslash_escapes in (False, True):
            uncommented = _without_comments(text, backslash_escapes)
            # A `WHERE` inside a literal (`DELETE FROM t RETURNING 'WHERE'`) is data:
            # also match with quoted text blanked, keeping it for the keywords above.
            unquoted = _without_comments(text, backslash_escapes, blank_quoted=True)
            match = _SQL_DESTRUCTIVE.search(uncommented) or _SQL_DESTRUCTIVE.search(unquoted) \
                or _NOSQL.search(uncommented)
            if match:
                break
        if match:
            return f"{command.name}: {match.group(0).strip()}"
    return None


def _database_drop_tools(command: SimpleCommand, ctx: RuleContext) -> str | None:
    if command.name in ("dropdb", "dropuser"):
        return command.name
    if command.name in ("mysqladmin", "mariadb-admin") and any(a in ("drop", "flush-tables") for a in command.args):
        return f"{command.name} drop"
    return None


_INFRA_HINT = (
    "This deletes infrastructure or data that is not in version control. Show the user the "
    "exact command and target and let them run it, or use the tool's dry-run/plan mode first."
)

INFRA_RULES: tuple[DangerRule, ...] = (
    DangerRule("terraform-destroy", HIGH, "destroys managed infrastructure or state",
               _INFRA_HINT + " (`terraform plan -destroy`)", _terraform),
    DangerRule("aws-delete", HIGH, "deletes AWS resources", _INFRA_HINT, _aws),
    DangerRule("gcloud-delete", HIGH, "deletes Google Cloud resources", _INFRA_HINT, _gcloud),
    DangerRule("az-delete", HIGH, "deletes Azure resources", _INFRA_HINT, _az),
    DangerRule("kubectl-delete-broad", HIGH, "deletes a namespace, cluster-scoped objects or everything matching",
               _INFRA_HINT + " (`kubectl delete --dry-run=server`)", _kubectl_delete_broad),
    DangerRule("docker-prune-volumes", HIGH, "deletes container volumes and their data", _INFRA_HINT,
               _docker_prune),
    DangerRule("sql-destructive", HIGH, "drops or truncates database objects or data", _INFRA_HINT,
               _destructive_sql),
    DangerRule("db-drop-tool", HIGH, "drops a database or user", _INFRA_HINT, _database_drop_tools),
    # Broad delete verbs the tables above do not scope: worth a human look.
    DangerRule("cloud-delete", HIGH, "a cloud CLI delete/terminate verb", _INFRA_HINT, _aws_generic,
               decision="ask"),
    DangerRule("gcloud-delete-other", HIGH, "a gcloud delete/destroy verb", _INFRA_HINT, _gcloud_generic,
               decision="ask"),
    DangerRule("az-delete-other", HIGH, "an az delete/purge verb", _INFRA_HINT, _az_generic, decision="ask"),
    DangerRule("kubectl-delete", HIGH, "deletes Kubernetes resources or a Helm release", _INFRA_HINT,
               _kubectl_delete, decision="ask"),
)
