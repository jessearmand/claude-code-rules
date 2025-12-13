# Codebase Analysis

Leverage Gemini's 1M token context window for comprehensive code analysis.

## Basic Patterns

### Single File Analysis

```bash
cd /path/to/project
gemini -p "@src/main.py Explain this file's purpose and structure"
```

### Multiple Files

```bash
gemini -p "@package.json @src/index.js Analyze the dependencies used in the code"
```

### Entire Directory

```bash
gemini -p "@src/ Summarize the architecture of this codebase"
```

### Multiple Directories

```bash
gemini -p "@src/ @tests/ Analyze test coverage for the source code"
```

### Entire Project

```bash
gemini -p "@./ Give me an overview of this entire project"

# Alternative with explicit flag
gemini --all_files -p "Analyze the project structure and dependencies"
```

## Implementation Verification

Use these patterns to check if features are implemented.

### Feature Detection

```bash
# Dark mode
gemini -p "@src/ @lib/ Has dark mode been implemented? Show relevant files and functions"

# Authentication
gemini -p "@src/ @middleware/ Is JWT authentication implemented? List all auth-related endpoints"

# WebSocket hooks
gemini -p "@src/ Are there React hooks that handle WebSocket connections? List them with paths"
```

### Security Auditing

```bash
# Error handling
gemini -p "@src/ @api/ Is proper error handling implemented for API endpoints? Show try-catch examples"

# Rate limiting
gemini -p "@backend/ @middleware/ Is rate limiting implemented? Show implementation details"

# SQL injection protection
gemini -p "@src/ @api/ Are SQL injection protections implemented? Show input sanitization"
```

### Infrastructure Checks

```bash
# Caching
gemini -p "@src/ @lib/ @services/ Is Redis caching implemented? List cache functions and usage"

# Test coverage
gemini -p "@src/payment/ @tests/ Is the payment module fully tested? List all test cases"
```

## Best Practices

1. **Be specific**: Instead of "analyze this code", ask "explain the authentication flow"
2. **Include context**: Mention what you're looking for to get focused results
3. **Target directories**: Only include relevant directories to reduce noise
4. **Verify paths**: Paths are relative to your current working directory

## When to Use

- Analyzing entire codebases or large directories
- Comparing multiple large files
- Understanding project-wide patterns or architecture
- Files totaling more than 100KB
- Checking for coding patterns across the entire codebase
- Verifying implementations before refactoring
