# General

Write a high quality, general purpose solution. Implement a solution that works correctly for all valid inputs and actions if they involve user interface, not just the test cases. Do not hard-code values or create solutions that only work for specific test inputs. Instead, implement the actual logic that solves the problem generally.

Focus on understanding the problem requirements and implementing the correct algorithm. The purpose of tests is to verify correctness, not to define the solution. Provide a principled implementation that follows best practices and software design principles.

Ask for clarifications if the task is unreasonable or infeasible, or if any of the tests are incorrect. The solution should be robust, maintainable, and extendable.

# Tool Use

- Only use `rg` for recursive search, manual or automatic filtering of plain text output
- Use the `gemini-agent` skill for large context analysis (1M token window)

# Development and Coding Styles

- Use 4-space indentation, unless project or editor settings is configured otherwise
- Be precise and targeted in examining where changes need to be made
- Avoid inline styles, create separate CSS files
- Avoid creating a large file, split it into reusable modules
- Refactor long functions into smaller, reusable functions

Language-specific guidance is provided via skills that are loaded when relevant:
- `lang-rust` - Rust with cargo/clippy
- `lang-python` - Python with uv/ruff/ty
- `lang-typescript` - TypeScript/JavaScript with Vitest/React
- `lang-swift` - Swift/SwiftUI development
- `xcode-build` - Xcode build, test, and profiling

# Available Tools

## gemini-cli

Use the `gemini-agent` skill for large context analysis with Gemini's 1M token window.

**Capabilities:**
- Codebase analysis (entire projects, 100KB+ files)
- Image and document understanding (PDFs, screenshots)
- Built-in web search
- Headless mode for scripting

**Quick reference:**
```bash
gemini -p "@path/to/file Explain this"    # Include file
gemini -p "@src/ Analyze architecture"    # Include directory
cat file | gemini -p "Summarize this"     # Pipe input
```

See skill documentation for detailed usage patterns.

## ripgrep

ripgrep is a command line tool that searches your files for patterns that you give it. ripgrep behaves as if reading each file line by line.

How to find all function definitions whose name is `write`:

```
$ rg 'fn write\('
src/printer.rs
469:    fn write(&mut self, buf: &[u8]) {

```
