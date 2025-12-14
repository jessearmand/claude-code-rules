---
name: lang-swift
description: Modern Swift and SwiftUI development with native state management, async/await patterns, and Apple platform conventions. Use when writing or reviewing Swift code.
---

# Swift & SwiftUI Development

Write idiomatic SwiftUI code following Apple's architectural recommendations.

## Core Philosophy

- SwiftUI is the default UI paradigm - embrace its declarative nature
- Avoid legacy UIKit patterns and unnecessary abstractions
- Focus on simplicity, clarity, and native data flow
- Let SwiftUI handle complexity - don't fight the framework

## State Management

Use built-in property wrappers:

| Wrapper | Purpose |
|---------|---------|
| `@State` | Local, ephemeral view state |
| `@Binding` | Two-way data flow between views |
| `@Observable` | Shared state (iOS 17+) |
| `@ObservableObject` | Legacy shared state (pre-iOS 17) |
| `@Environment` | Dependency injection |

## Quick Example

```swift
struct CounterView: View {
    @State private var count = 0

    var body: some View {
        VStack {
            Text("Count: \(count)")
            Button("Increment") {
                count += 1
            }
        }
    }
}
```

## DO:

- Write self-contained views
- Use property wrappers as intended
- Handle loading/error states explicitly
- Keep views focused on presentation
- Use Swift's type system for safety

## DON'T:

- Create ViewModels for every view
- Move state out of views unnecessarily
- Add abstraction without clear benefit
- Use Combine for simple async operations
- Fight SwiftUI's update mechanism

## Modern Swift Features

- Use Swift Concurrency (`async/await`, actors)
- Leverage Swift 6 data race safety
- Embrace value types where appropriate
- Use protocols for abstraction, not just testing

## Detailed Guides

- [Patterns](patterns.md) - State, async, and composition patterns
- [Logging](logging.md) - Swift logging conventions
- [Concurrency](concurrency.md) - Swift 6 strict concurrency and data race safety
