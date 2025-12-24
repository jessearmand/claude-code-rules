# Swift Concurrency: Testing, Debugging, and Common Errors
This is part of the Swift concurrency reference split for progressive disclosure.
See `concurrency.md` for the index.

## Testing and Debugging

### Thread Sanitizer (TSan)

Thread Sanitizer remains essential for catching runtime data races that escape compile-time checks:

```bash
# Enable in Xcode
Product -> Scheme -> Edit Scheme -> Diagnostics -> Thread Sanitizer

# Or via command line
swift test -Xswiftc -sanitize=thread
```

**When to use TSan:**
- Testing legacy code with @preconcurrency imports
- Verifying @unchecked Sendable implementations
- Catching races in C/Objective-C interop

### Swift Concurrency Debugger (Xcode 16+)

New debugging tools for concurrent code:

1. **Task Tree View**: Visualize parent-child task relationships
2. **Actor Memory Graph**: See actor isolation boundaries
3. **Hop Tracking**: Follow execution across isolation domains

```swift
// Debugging helpers
extension Task {
    static func currentPriority() -> TaskPriority {
        Task.currentPriority
    }

    static func printTaskTree() {
        // Available in debug builds
        #if DEBUG
        print("Task: \(Task<Never, Never>.currentPriority)")
        #endif
    }
}
```

### Unit Testing Concurrent Code

```swift
// Test helper for async code with timeout
func withTimeout<T>(
    _ duration: Duration = .seconds(5),
    operation: @escaping () async throws -> T
) async throws -> T {
    try await withThrowingTaskGroup(of: T.self) { group in
        group.addTask {
            try await operation()
        }

        group.addTask {
            try await Task.sleep(for: duration)
            throw TimeoutError()
        }

        let result = try await group.next()!
        group.cancelAll()
        return result
    }
}

// Testing actor isolation
final class ActorTests: XCTestCase {
    func testActorIsolation() async throws {
        let actor = TestActor()

        // Verify isolation with multiple concurrent operations
        try await withThrowingTaskGroup(of: Int.self) { group in
            for i in 0..<100 {
                group.addTask {
                    await actor.increment()
                    return await actor.value
                }
            }

            var results: Set<Int> = []
            for try await result in group {
                results.insert(result)
            }

            // All results should be unique if properly isolated
            XCTAssertEqual(results.count, 100)
        }
    }
}
```

### Debugging Common Issues

```swift
// 1. Debugging unexpected suspension points
actor DataManager {
    func debugSuspension() async {
        print("Before suspension: \(Thread.current)")
        await someAsyncOperation()
        print("After suspension: \(Thread.current)") // May be different thread
    }
}

// 2. Tracking isolation context
func debugIsolation(
    isolation: isolated (any Actor)? = #isolation
) async {
    if let isolation {
        print("Running on: \(type(of: isolation))")
    } else {
        print("Running on non-isolated context")
    }
}

// 3. Detecting priority inversions
Task(priority: .low) {
    await debugPriority() // May run at higher priority due to escalation
}

func debugPriority() async {
    print("Current priority: \(Task.currentPriority)")
}
```

## Common Compiler Errors

### Error Reference Table

| Diagnostic | Example | Fix |
|------------|---------|-----|
| **Non-Sendable type crossing actor boundary** | `Capture of 'nonSendable' with non-sendable type 'MyClass'` | 1. Make type Sendable<br>2. Use `sending` parameter<br>3. Copy/transform to Sendable type |
| **Actor-isolated property referenced from non-isolated** | `Actor-isolated property 'items' can not be referenced from a non-isolated context` | 1. Add `await`<br>2. Move code to actor<br>3. Make property `nonisolated` |
| **Call to main actor-isolated from non-isolated** | `Call to main actor-isolated instance method 'updateUI()' in a synchronous nonisolated context` | 1. Add `@MainActor` to caller<br>2. Use `await MainActor.run { }`<br>3. Make method `nonisolated` |
| **Mutation of captured var** | `Mutation of captured var 'counter' in concurrently-executing code` | 1. Use actor for state<br>2. Make immutable<br>3. Use `Mutex` (Swift 6.1+) |
| **Sendable closure captures non-Sendable** | `Capture of 'self' with non-sendable type 'ViewController?' in a `@Sendable` closure` | 1. Use `[weak self]`<br>2. Make type Sendable<br>3. Extract needed values before closure |

### Detailed Error Solutions

#### 1. Non-Sendable Type Errors

```swift
// Bad: Non-Sendable type 'UIImage' crossing actor boundary
class ImageProcessor {
    func process(image: UIImage) async {
        Task.detached {
            // Error: capture of 'image' with non-sendable type
            manipulate(image)
        }
    }
}

// Good: Solution 1: Use sending parameter
class ImageProcessor {
    func process(image: sending UIImage) async {
        Task.detached {
            manipulate(image) // Ownership transferred
        }
    }
}

// Good: Solution 2: Convert to Sendable representation
class ImageProcessor {
    func process(image: UIImage) async {
        let imageData = image.pngData()! // Data is Sendable
        Task.detached {
            let recreated = UIImage(data: imageData)!
            manipulate(recreated)
        }
    }
}
```

#### 2. Actor Isolation Errors

```swift
// Bad: Actor-isolated property accessed without await
actor DataStore {
    var items: [Item] = []

    nonisolated func getItemCount() -> Int {
        items.count // Error: actor-isolated property
    }
}

// Good: Solution 1: Make method async
actor DataStore {
    var items: [Item] = []

    func getItemCount() async -> Int {
        items.count // OK: implicitly isolated to actor
    }
}

// Good: Solution 2: Use computed property
actor DataStore {
    private var items: [Item] = []

    var itemCount: Int {
        items.count // OK: computed property is isolated
    }
}
```

#### 3. MainActor Isolation Errors

```swift
// Bad: Call to MainActor-isolated from background
func backgroundWork() {
    updateUI() // Error: MainActor-isolated
}

@MainActor
func updateUI() { }

// Good: Solution 1: Make caller MainActor
@MainActor
func backgroundWork() async {
    await fetchData()
    updateUI() // OK: both on MainActor
}

// Good: Solution 2: Explicit MainActor.run
func backgroundWork() async {
    let data = await fetchData()
    await MainActor.run {
        updateUI()
    }
}
```
