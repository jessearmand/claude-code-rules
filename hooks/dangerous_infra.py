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
from shell_parse import SimpleCommand

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
    if command.name == "helm" and positional_words(command)[:1] in (["uninstall"], ["delete"], ["del"], ["un"]):
        return "helm uninstall"
    return None


def _docker_prune(command: SimpleCommand, ctx: RuleContext) -> str | None:
    if command.name not in ("docker", "podman"):
        return None
    words = positional_words(command)
    if words[:2] == ["system", "prune"] and (short_flag(command.args, "a") or long_flag(command.args, "--all", "--volumes")):
        return f"{command.name} system prune --all/--volumes"
    if words[:1] == ["volume"] and len(words) > 1 and words[1] in ("rm", "prune", "remove"):
        return f"{command.name} volume {words[1]}"
    return None


# --------------------------------------------------------------------------- #
# Databases
# --------------------------------------------------------------------------- #

_SQL_DESTRUCTIVE = re.compile(
    r"\b(?:DROP\s+(?:DATABASE|SCHEMA|TABLE|USER|ROLE)|TRUNCATE\b|DELETE\s+FROM\s+\S+\s*(?:;|$)"
    r"|ALTER\s+TABLE\s+\S+\s+DROP\b)",
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


def _sql_texts(command: SimpleCommand) -> list[str]:
    flags = _SQL_CLIENTS.get(command.name)
    if flags is None:
        if command.name in ("mongosh", "mongo"):
            return [a for i, a in enumerate(command.args) if i and command.args[i - 1] == "--eval"]
        if command.name == "redis-cli":
            return [" ".join(command.args)]
        return []
    texts = [a.split("=", 1)[1] for a in command.args if any(a.startswith(f + "=") for f in flags if f.startswith("--"))]
    texts += [command.args[i + 1] for i, a in enumerate(command.args[:-1]) if a in flags]
    if command.name == "sqlite3":
        texts += [a for a in operands(command.args)[1:]]
    texts += [r.body or r.target for r in command.redirects if r.op in ("<<", "<<-", "<<<")]
    return texts


def _destructive_sql(command: SimpleCommand, ctx: RuleContext) -> str | None:
    for text in _sql_texts(command):
        match = _SQL_DESTRUCTIVE.search(text) or _NOSQL.search(text)
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
