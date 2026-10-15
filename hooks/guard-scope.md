# Bash guard review boundary

These hooks cover Bash on Linux and macOS. The objective is a decision layer
above deterministic guards: deterministic denials take precedence, the model
may only ask, and unavailable model results return to normal host permissions.

The comparison in `cc-safety-net-coverage.md` (upstream cc-safety-net 2.6.11,
commit `0626be7a791c17a0daa67ae90a899372c667056d`) establishes family coverage,
not complete command-spelling or product parity.

The current follow-up fixes stay within existing contracts:

- Destructive Git: direct ref replacement, URL-scoped program overrides, and
  bounded numbered environment configuration using the existing alias/override rules.
- Nested execution: known database-client stdin, process-substitution assignments,
  `source --` filenames and positional arguments, direct interpreter strings,
  and equals-form long code options reach their existing scans.
- Existing SQL controls: reuse bounded stdin resolution through pass-through
  `cat` stages and recognize destructive DROP operations across object types.
- Existing publishing controls: retain confirmation for release asset uploads
  and allow read-only ownership and registry queries.
- Model context: verify the existing no-space prose redaction at the outbound
  payload boundary, preserving non-secret path references.

SQL and publishing are existing local extensions beyond upstream core. These
fixes do not add new guard families. File-derived programs and SQL retain ask
provenance; typed deterministic denials cannot be overridden by the model.

Residual scope remains bounded static analysis: unresolved dynamic sources,
unmodeled producers and stdin beyond the depth budget are not evidence of safe
execution. Full Bash emulation, Windows shells, upstream policy presets and GUI
parity, new adapters, and broader uncertainty-routing changes are separate work.
Live model accuracy and real pi/omp sessions require their own validation.
