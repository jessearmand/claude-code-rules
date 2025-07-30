---
allowed-tools: Bash(gemini:*), Bash(cd:*), Bash(ls:*), Bash(pwd:*)
description: Use `gemini` to analyze large codebases with massive context window
---

# Analyze large codebase with gemini-cli

When you need to debug issues involving a large size of related code relationships in the codebase, or when analyzing entire project, use `gemini`

## Usage

Use `gemini -p` to leverage Gemini's large context capacity. The `@` syntax includes files and directories in your prompts. Paths should be relative to WHERE you run the gemini command.

### Single file analysis:

```bash
cd /path/to/project
gemini -p "@src/main.py Explain this file's purpose and structure"
```

### Multiple files:

```bash
gemini -p "@package.json @src/index.js Analyze the dependencies used in the code"
```

### Entire directory:

```bash
gemini -p "@src/ Summarize the architecture of this codebase"
```

### Multiple directories:

```bash
gemini -p "@src/ @tests/ Analyze test coverage for the source code"
```

### Current directory and subdirectories:

```bash
gemini -p "@./ Give me an overview of this entire project"
# Or use --all_files flag:
gemini --all_files -p "Analyze the project structure and dependencies"
```

## Implementation Verification Examples

### Check if a feature is implemented:

```bash
gemini -p "@src/ @lib/ Has dark mode been implemented in this codebase? Show me the relevant files and functions"
```

### Verify authentication:

```bash
gemini -p "@src/ @middleware/ Is JWT authentication implemented? List all auth-related endpoints and middleware"
```

### Check for specific patterns:

```bash
gemini -p "@src/ Are there any React hooks that handle WebSocket connections? List them with file paths"
```

### Verify error handling:

```bash
gemini -p "@src/ @api/ Is proper error handling implemented for all API endpoints? Show examples of try-catch blocks"
```

### Check for rate limiting:

```bash
gemini -p "@backend/ @middleware/ Is rate limiting implemented for the API? Show the implementation details"
```

### Verify caching strategy:

```bash
gemini -p "@src/ @lib/ @services/ Is Redis caching implemented? List all cache-related functions and their usage"
```

### Check security measures:

```bash
gemini -p "@src/ @api/ Are SQL injection protections implemented? Show how user inputs are sanitized"
```

### Verify test coverage:

```bash
gemini -p "@src/payment/ @tests/ Is the payment processing module fully tested? List all test cases"
```

## When to Use Gemini CLI

Use `gemini -p` when:
- Analyzing entire codebases or large directories
- Comparing multiple large files
- Need to understand project-wide patterns or architecture
- Current context window is insufficient for the task
- Working with files totaling more than 100KB, PDF documents, or images
- Verifying if specific features, patterns, or security measures are implemented
- Checking for the presence of certain coding patterns across the entire codebase

## Important Notes

- Paths in @ syntax are relative to your current working directory when invoking gemini
- The CLI will include file contents directly in the context
- No need for --yolo flag for read-only analysis
- Gemini's context window can handle entire codebases
- When checking implementations, be specific about what you're looking for to get accurate results

## Your Analysis Task

$ARGUMENTS
