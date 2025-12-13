# Headless Mode

Run Gemini CLI non-interactively for scripting and automation.

## Core Flags

| Flag | Description |
|------|-------------|
| `-p`, `--prompt` | Run in headless mode with your query |
| `--output-format` | Output as `text` (default), `json`, or `stream-json` |
| `-y`, `--yolo` | Auto-approve all actions |
| `--approval-mode` | Set approval behavior (e.g., `auto_edit`) |
| `-m`, `--model` | Select which Gemini model to use |
| `-d`, `--debug` | Enable debug mode |

## Input Methods

### Direct Prompt

```bash
gemini -p "Your question here"
```

### Stdin Piping

```bash
echo "Explain this concept" | gemini

cat file.md | gemini -p "Summarize this"

git diff | gemini -p "Write a commit message"
```

### File Inclusion

```bash
gemini -p "@src/file.py Review this code"
```

## Output Formats

### Text (Default)

```bash
gemini -p "Explain REST APIs"
# Returns: Human-readable text response
```

### JSON

```bash
gemini -p "List 5 programming languages" --output-format json
```

Returns structured data:
```json
{
  "response": "...",
  "statistics": {
    "models": [...],
    "tools": [...],
    "files": [...]
  }
}
```

### Stream JSON

```bash
gemini -p "Long analysis" --output-format stream-json
```

Emits newline-delimited events:
- `init` - Session started
- `message` - Response chunks
- `tool_use` - Tool invocations
- `tool_result` - Tool outputs
- `error` - Error information
- `result` - Final result

## Practical Examples

### Code Review

```bash
cat src/file.py | gemini -p "Review this code for security issues"
```

### Commit Messages

```bash
git diff --staged | gemini -p "Write a concise commit message" --output-format json
```

### Batch File Analysis

```bash
for file in src/*.py; do
  gemini -p "@$file Analyze this file" > "analysis_$(basename $file).txt"
done
```

### Log Analysis

```bash
grep "ERROR" /var/log/app.log | gemini -p "Analyze these errors and suggest fixes"
```

### Release Notes Generation

```bash
git log v1.0.0..HEAD --oneline | gemini -p "Generate release notes from these commits"
```

### CI/CD Integration

```bash
# In GitHub Actions or similar
gemini -p "@src/ Check for security vulnerabilities" --output-format json > security-report.json
```

## Output Redirection

Standard shell operators work:

```bash
# Save to file
gemini -p "@src/ Analyze" > analysis.txt

# Append to file
gemini -p "Additional analysis" >> analysis.txt

# Pipe to another command
gemini -p "Generate SQL" | psql database
```

## Tips

1. **Use JSON for parsing**: When you need to process the output programmatically
2. **Stream for long tasks**: Use `stream-json` for real-time progress monitoring
3. **Combine with shell tools**: Pipe output through `jq`, `grep`, or other tools
4. **Auto-approve carefully**: Only use `-y` when you trust the operations
