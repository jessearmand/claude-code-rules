# Tool Use

- Use `ast-grep` or `sg` for code structural search, lint or rewriting, cheatsheet: @~/.claude/ast-grep-rule.md
- Only use `rg` for recursive search, manual or automatic filtering of plain text output
- Use `gemini` to prompt for more understanding in large documents, images, or directory of files

# Guidelines

- Use 4-space indentation, unless project or editor settings is configured otherwise
- Be precise and targeted in examining where changes need to be made
- Avoid inline styles, create separate CSS files
- Avoid creating a large file, split it into reusable modules
- Refactor long functions into smaller, reusable functions
- Follow development guidelines for files written in:
  - python: @~/.claude/python-guidelines.md
  - javascript / typescript: @~/.claude/typescript-guide.md
  - swift: @~/.claude/modern-swift.md

# Available Tools

## gemini-cli

`gemini` is available to prompt for more understanding

To prompt a text with `gemini`

```
echo "Explain the functions on @path/to/source_file.rs, and what do they achieve" | gemini
```

```
gemini -p "Explain the functions on @path/to/source_file.rs, and what do they achieve"
```

```
gemini -p "Analyze this document @path/to/document"
```

## ast-grep

`ast-grep` or `sg` is available as CLI tool for code structural search, lint, and rewriting

ast-grep has following form
```
sg --pattern 'var code = $PATTERN' --rewrite 'let code = new $PATTERN' --lang ts
```

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
xcodebuild -configuration Debug -scheme $SCHEME-Package -sdk iphonesimulator -destination "platform=iOS Simulator,name=iPhone 16" test | xcbeautify
```
- Run test for macOS target, where `-destination` argument value must be a concrete device given by `name=My Mac`. A Swift package typically have `-Package` suffix, resulting in `$SCHEME-Package` as a `-scheme` argument value
```
xcodebuild -configuration Debug -scheme $SCHEME-Package -destination "platform=macOS,name=My Mac" test | xcbeautify
```

