# General

Write a high quality, general purpose solution. Implement a solution that works correctly for all valid inputs and actions if they involve user interface, not just the test cases. Do not hard-code values or create solutions that only work for specific test inputs. Instead, implement the actual logic that solves the problem generally.

Focus on understanding the problem requirements and implementing the correct algorithm. The purpose of tests is to verify correctness, not to define the solution. Provide a principled implementation that follows best practices and software design principles.

Ask for clarifications if the task is unreasonable or infeasible, or if any of the tests are incorrect. The solution should be robust, maintainable, and extendable.

# Tool Use

- Do not use `/tmp` as a scratchpad, create a `scratchpad` directory in the current working directory of a project
- If you don't have write access to the current working directory, ask the user to create scratchpad directory with proper permission

# Development and Coding Styles

- Use standardized code formatter and linter, based on the project context
- Avoid inline styles, create separate CSS files
- Avoid creating a large file, split it into reusable modules
- Refactor long functions into smaller, reusable functions

Language-specific guidance is provided via skills that are loaded when relevant:
- `lang-rust` - Rust with cargo/clippy
- `lang-python` - Python with uv/ruff/ty
- `lang-typescript` - TypeScript/JavaScript with Vitest/React
- `lang-swift` - Swift/SwiftUI development
- `xcode-build` - Xcode build, test, and profiling

