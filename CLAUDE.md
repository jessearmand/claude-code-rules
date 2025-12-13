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

## Rust

- Run `cargo check` after an implementation is ready to be tested
- Run `cargo clippy` after `cargo check` or `cargo build` produced no errors
- Refer to @~/.claude/clippy-usage.md

## Swift, Python, TypeScript

- Follow development guides for files written in:
  - python: @~/.claude/python-guide.md
  - javascript / typescript: @~/.claude/typescript-guide.md
  - swift: @~/.claude/modern-swift.md

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

## Xcode

- Use `xcbeautify` to pipe the output of `xcodebuild`
```
set -o pipefail && xcodebuild [flags] | xcbeautify
```

- List available simulators before running `xcodebuild` on a simulator
```
xcrun simctl list
```
- Run build for iOS simulator target, where `-destination` argument is set to a device such as `name=iPhone 16`, `-scheme` argument value is one from the list given by `xcodebuild -list`, use Debug configuration before building with Release configuration. Use available build configurations from the output. `$PROJECT_NAME` should be the name of the Xcode project in the current working directory
```
set -o pipefail && xcodebuild -workspace $PROJECT_NAME.xcodeproj/project.xcworkspace -configuration Debug -scheme $SCHEME -sdk iphonesimulator -destination "platform=iOS Simulator,name=iPhone 16" | xcbeautify
```
- Run test for iOS simulator target, where `-destination` argument value must be a concrete device given by `name=iPhone 16`. A Swift package typically have `-Package` suffix, resulting in `$SCHEME-Package` as a `-scheme` argument value
```
set -o pipefail && xcodebuild -configuration Debug -scheme $SCHEME-Package -sdk iphonesimulator -destination "platform=iOS Simulator,name=iPhone 16" test | xcbeautify
```
- Run test for macOS target, where `-destination` argument value must be a concrete device given by `name=My Mac`. A Swift package typically have `-Package` suffix, resulting in `$SCHEME-Package` as a `-scheme` argument value
```
set -o pipefail && xcodebuild -configuration Debug -scheme $SCHEME-Package -destination "platform=macOS,name=My Mac" test | xcbeautify
```
- Run test for visionOS target where `-destination` argument value must be a concrete device given by `name="Apple Vision Pro`
```
set -o pipefail && xcodebuild -configuration Debug -scheme $SCHEME-Package -sdk xrsimulator -destination "platform=visionOS Simulator,name=Apple Vision Pro" test | xcbeautify
```

## Logging

For logging inside a Swift module, use `Logger` according to the convention of the library, where `FileServer` can be a filename, `class` or `struct` where the `logger` is declared

```
import os

private let logger = Logger(subsystem: Bundle.main.bundleIdentifier ?? "FileServer", category: "FileSystemService")

```

- Prefer `debugPrint` over `print` when the purpose of printing is for debugging
- When logging a command line tool written in Swift, prefer printing to `stdout` instead of Apple's unified logging

