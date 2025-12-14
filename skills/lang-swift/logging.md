# Swift Logging

Logging conventions for Swift applications.

## Apple's Unified Logging (Libraries/Apps)

Use `Logger` for structured logging in apps and libraries:

```swift
import os

private let logger = Logger(
    subsystem: Bundle.main.bundleIdentifier ?? "FileServer",
    category: "FileSystemService"
)
```

### Usage

```swift
logger.debug("Processing file: \(filename)")
logger.info("Operation completed")
logger.warning("Retry attempt \(count)")
logger.error("Failed to load: \(error.localizedDescription)")
```

### Log Levels

| Level | Purpose |
|-------|---------|
| `debug` | Development debugging |
| `info` | General information |
| `notice` | Notable events |
| `warning` | Potential issues |
| `error` | Errors |
| `fault` | Critical failures |

## Command Line Tools

For CLI tools, prefer `stdout` over unified logging:

```swift
// Prefer for CLI output
print("Processing complete")

// For debugging during development
debugPrint(complexObject)
```

## Debugging

Prefer `debugPrint` over `print` for debugging:

```swift
// debugPrint shows more detail
debugPrint(user)  // User(id: "1", name: "Alice")

// print uses description
print(user)       // Alice
```

## Best Practices

- Use unified logging (`Logger`) in apps and frameworks
- Use `print`/`debugPrint` for CLI tools
- Prefer `debugPrint` when the purpose is debugging
- Include context in log messages (file names, IDs, counts)
- Use appropriate log levels
- Don't log sensitive information
