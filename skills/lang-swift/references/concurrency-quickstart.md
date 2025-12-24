# Swift Concurrency: Quickstart and Core Concepts
This is part of the Swift concurrency reference split for progressive disclosure.
See `concurrency.md` for the index.

## Introduction

Swift 6 represents a revolutionary leap in concurrent programming, introducing compile-time data race safety that eliminates an entire class of bugs. This comprehensive guide covers all Swift Evolution proposals, practical examples, and migration strategies for adopting Swift 6's strict concurrency model.

### Quick Start: Enable Strict Concurrency

**Xcode:**
```
Build Settings -> Swift Compiler - Language -> Strict Concurrency Checking -> Complete
```

**Command Line:**
```bash
swiftc -strict-concurrency=complete -swift-version 6 MyFile.swift
```

**Package.swift:**
```swift
.target(
    name: "MyTarget",
    swiftSettings: [
        .enableUpcomingFeature("StrictConcurrency"),
        .swiftLanguageMode(.v6)  // or stay on .v5 with warnings
    ]
)
```

### What is Swift 6 Concurrency?

Swift 6's concurrency model builds upon Swift 5.5's async/await foundation with:
- **Complete data race safety** at compile time
- **Actor isolation** enforcement
- **Sendable protocol** requirements
- **Region-based isolation** for smarter type checking
- **Improved ergonomics** reducing false positives

### Key Benefits

1. **Compile-time Safety**: Catch data races before runtime
2. **Progressive Migration**: Adopt incrementally with warnings first
3. **Better Performance**: Compiler optimizations enabled by isolation guarantees
4. **Clearer Intent**: Explicit concurrency boundaries in code

## Core Concepts

### 1. Isolation Domains

Swift 6 defines clear isolation domains where data can be safely accessed:

```swift
// MainActor isolation domain - UI code
@MainActor
class ViewController: UIViewController {
    var label = UILabel() // Safe within MainActor

    func updateUI() {
        label.text = "Updated" // No await needed - same isolation
    }
}

// Custom actor isolation domain
actor DataManager {
    private var cache: [String: Data] = [:] // Protected by actor

    func store(key: String, data: Data) {
        cache[key] = data // Safe within actor
    }
}

// No isolation - must be Sendable
struct Point: Sendable {
    let x: Double
    let y: Double
}
```

### 2. Concurrency Boundaries

Data crossing concurrency boundaries must be Sendable:

```swift
// Bad: Swift 5 - Potential data race
class Model {
    var items: [Item] = []
}

func process(model: Model) async {
    await Task.detached {
        model.items.append(Item()) // Data race!
    }.value
}

// Good: Swift 6 - Compile-time error prevents data race
@MainActor
final class Model: Sendable {
    private(set) var items: [Item] = []

    func addItem(_ item: Item) {
        items.append(item) // Safe - MainActor synchronized
    }
}
```

### 3. Region-Based Isolation

Swift 6 introduces "isolation regions" that track data flow:

```swift
// Region-based isolation allows safe transfer without Sendable
func processImage(_ image: UIImage) async -> ProcessedImage {
    // Swift 6 proves image won't be accessed after transfer
    let processed = await withTaskGroup(of: ProcessedTile.self) { group in
        for tile in image.tiles {
            group.addTask {
                // Safe transfer - compiler tracks regions
                await processTile(tile)
            }
        }
        // Collect results...
    }
    return processed
}
```
