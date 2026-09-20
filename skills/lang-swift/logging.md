# Swift logging

Preserve the project's logging abstraction and supported platforms. Use Apple's
unified logging for Apple platform apps when it fits the project; do not introduce
an Apple-only logging dependency into a cross-platform package incidentally.

For command-line tools, distinguish user-facing output from diagnostics. Preserve
machine-readable stdout contracts and route diagnostics to the existing logger or
stderr as appropriate. Do not add `print` or `debugPrint` calls that corrupt command
output or remain as unrelated debugging noise.

Choose levels that reflect operational meaning: debug details, normal progress,
recoverable warnings, and failures. Include useful context while preserving the
project's privacy and redaction rules; do not log secrets or sensitive payloads.
