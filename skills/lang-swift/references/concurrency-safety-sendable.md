# Swift Concurrency: Data Race Safety and Sendable
This is part of the Swift concurrency reference split for progressive disclosure.
See `concurrency.md` for the index.

## Data Race Safety

### Complete Concurrency Checking

Swift 6 enforces complete checking by default:

```swift
// Enable in Swift 5 mode for migration
// swift -strict-concurrency=complete

// Levels of checking:
// 1. minimal - Only explicit Sendable conformances
// 2. targeted - Infer Sendable for some types
// 3. complete - Full data race checking
```

### Common Data Race Patterns and Fixes

#### Pattern 1: Shared Mutable State

```swift
// Bad: Data race
class Counter {
    var value = 0

    func increment() {
        value += 1 // Race condition!
    }
}

// Good: Fix 1: Use an actor
actor Counter {
    private var value = 0

    func increment() {
        value += 1 // Actor-isolated
    }

    var currentValue: Int {
        value
    }
}

// Good: Fix 2: Use atomic operations
import Atomics

final class Counter: Sendable {
    private let value = ManagedAtomic<Int>(0)

    func increment() {
        value.wrappingIncrement(ordering: .relaxed)
    }

    var currentValue: Int {
        value.load(ordering: .relaxed)
    }
}
```

#### Pattern 2: Callback Isolation

```swift
// Bad: Unclear isolation
class NetworkManager {
    func fetch(completion: @escaping (Data) -> Void) {
        URLSession.shared.dataTask(with: url) { data, _, _ in
            completion(data!) // What thread?
        }
    }
}

// Good: Clear isolation with async/await
class NetworkManager {
    func fetch() async throws -> Data {
        let (data, _) = try await URLSession.shared.data(from: url)
        return data
    }
}

// Good: Or explicit MainActor isolation
class NetworkManager {
    func fetch(completion: @MainActor @escaping (Data) -> Void) {
        Task {
            let data = try await URLSession.shared.data(from: url).0
            await completion(data)
        }
    }
}
```

## Sendable Protocol

### Understanding Sendable

```swift
// Sendable indicates thread-safe types
public protocol Sendable {}

// Automatic conformance for:
// 1. Actors (handle synchronization)
// 2. Immutable structs/enums
// 3. Final classes with immutable storage
// 4. @unchecked Sendable for manual safety

// Examples of automatic Sendable
struct Point: Sendable { // Implicit
    let x: Double
    let y: Double
}

enum Status: Sendable { // Implicit
    case pending
    case completed(at: Date)
}

actor DataManager {} // Implicitly Sendable

final class User: Sendable {
    let id: UUID
    let name: String
    // All stored properties are immutable
}
```

### Conditional Sendable

```swift
// Generic types can be conditionally Sendable
struct Container<T> {
    let value: T
}

// Automatic conditional conformance
extension Container: Sendable where T: Sendable {}

// Custom conditional conformance
struct Cache<Key: Hashable, Value> {
    private var storage: [Key: Value] = [:]
    private let lock = NSLock()
}

extension Cache: @unchecked Sendable where Key: Sendable, Value: Sendable {
    // We ensure thread safety with lock
}
```

### @unchecked Sendable

```swift
// For types that are thread-safe but can't be proven by compiler
final class ThreadSafeCache: @unchecked Sendable {
    private var cache: [String: Data] = [:]
    private let queue = DispatchQueue(label: "cache.queue")

    func get(_ key: String) -> Data? {
        queue.sync { cache[key] }
    }

    func set(_ key: String, value: Data) {
        queue.async { self.cache[key] = value }
    }
}

// Reference types with immutable data
final class ImageWrapper: @unchecked Sendable {
    let cgImage: CGImage

    init(cgImage: CGImage) {
        self.cgImage = cgImage
    }
}
```

### Sendable Functions and Closures

```swift
// Sendable function types
typealias AsyncOperation = @Sendable () async -> Void
typealias CompletionHandler = @Sendable (Result<Data, Error>) -> Void

// Using Sendable closures
func performAsync(operation: @Sendable @escaping () async -> Void) {
    Task {
        await operation()
    }
}

// Sendable captures
func createTimer(interval: TimeInterval) -> AsyncStream<Date> {
    AsyncStream { continuation in
        let timer = Timer.scheduledTimer(withTimeInterval: interval, repeats: true) { _ in
            continuation.yield(Date())
        }

        continuation.onTermination = { @Sendable _ in
            timer.invalidate() // Must be Sendable
        }
    }
}
```
