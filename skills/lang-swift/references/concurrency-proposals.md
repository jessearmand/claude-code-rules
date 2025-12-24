# Swift Concurrency: Evolution Proposals and Roadmap
This is part of the Swift concurrency reference split for progressive disclosure.
See `concurrency.md` for the index.

## Swift Evolution Proposals

### Foundation Proposals (Swift 5.5-5.10)

#### SE-0302: Sendable and @Sendable
Introduces the fundamental `Sendable` protocol:

```swift
// Types safe to share across concurrency domains
protocol Sendable {}

// Sendable closure
let operation: @Sendable () -> Void = {
    print("This closure captures only Sendable values")
}

// Conditional Sendable
struct Container<T>: Sendable where T: Sendable {
    let value: T
}
```

#### SE-0306: Actors
The actor model for protecting mutable state:

```swift
actor BankAccount {
    private var balance: Decimal = 0

    func deposit(amount: Decimal) {
        balance += amount
    }

    func withdraw(amount: Decimal) -> Bool {
        guard balance >= amount else { return false }
        balance -= amount
        return true
    }

    // Computed property accessible without await
    nonisolated var accountDescription: String {
        "Bank Account" // No state access
    }
}
```

#### SE-0316: Global Actors
System-wide isolation domains:

```swift
@globalActor
actor DataActor {
    static let shared = DataActor()
}

// Apply to entire type
@DataActor
class DataStore {
    var items: [Item] = []

    func add(_ item: Item) {
        items.append(item)
    }
}

// Apply to specific members
class MixedClass {
    @DataActor var data: [String] = []
    @MainActor var uiState = UIState()

    @DataActor
    func processData() async {
        // Runs on DataActor
    }

    @MainActor
    func updateUI() {
        // Runs on MainActor
    }
}
```

### Swift 6 Core Proposals

#### SE-0337: Incremental Migration to Concurrency Checking
Enables progressive adoption:

```swift
// Package.swift
.target(
    name: "MyTarget",
    swiftSettings: [
        .enableUpcomingFeature("StrictConcurrency"),
        .enableUpcomingFeature("CompleteAsync"),
        .enableExperimentalFeature("StrictConcurrency=minimal")
    ]
)

// Or via compiler flag
// swiftc -strict-concurrency=complete
```

#### SE-0401: Remove Actor Isolation Inference from Property Wrappers
Eliminates unexpected isolation:

```swift
// Before SE-0401
struct ContentView: View {
    @StateObject private var model = Model() // Made View MainActor-isolated!

    func doWork() { // Implicitly @MainActor
        // ...
    }
}

// After SE-0401
struct ContentView: View {
    @StateObject private var model = Model() // No isolation inference

    nonisolated func doWork() { // Explicitly non-isolated
        // ...
    }
}
```

#### SE-0412: Strict Concurrency for Global Variables
Global variable safety with `nonisolated(unsafe)`:

```swift
// Bad: Swift 6 error: global variable not concurrency-safe
var sharedCache: [String: Data] = [:]

// Good: Option 1: Make it a let constant
let sharedConstants = Constants()

// Good: Option 2: Use global actor
@MainActor
var sharedUICache: [String: UIImage] = [:]

// Good: Option 3: Actor isolation
actor CacheActor {
    static let shared = CacheActor()
    private var cache: [String: Data] = [:]
}

// Good: Option 4: Explicit unsafe opt-out
struct LegacyAPI {
    nonisolated(unsafe) static var shared: LegacyAPI?
}
```

#### SE-0414: Region-Based Isolation
Revolutionary improvement in isolation checking:

```swift
// Non-Sendable type can be safely transferred
class MutableData {
    var value: Int = 0
}

func process() async {
    let data = MutableData() // Non-Sendable

    // Good: Safe: data not used after transfer
    await withTaskGroup(of: Void.self) { group in
        group.addTask {
            data.value = 42 // Region analysis proves safety
        }
    }

    // Bad: Error: data used after transfer
    // print(data.value)
}
```

#### SE-0420: Inheritance of Actor Isolation
Dynamic isolation inheritance:

```swift
// Function inherits caller's isolation
func log(
    _ message: String,
    isolation: isolated (any Actor)? = #isolation
) async {
    print("[\(isolation)] \(message)")
}

@MainActor
func updateUI() async {
    await log("Updating UI") // Inherits MainActor isolation
}

actor DataProcessor {
    func process() async {
        await log("Processing") // Inherits DataProcessor isolation
    }
}
```

#### SE-0430: Sending Parameter and Result Values
Safe transfer without full Sendable:

```swift
// 'sending' allows ownership transfer
func processData(_ data: sending MutableData) async -> sending ProcessedData {
    // data is consumed - original reference invalidated
    return ProcessedData(from: data)
}

// Updated Task API
extension Task where Failure == Never {
    init(
        priority: TaskPriority? = nil,
        operation: sending @escaping () async -> Success
    )
}
```

#### SE-0431: @isolated(any) Function Types
Isolation-agnostic function types:

```swift
// Function type that preserves any isolation
typealias IsolatedOperation = @isolated(any) () async -> Void

struct Executor {
    func run(_ operation: IsolatedOperation) async {
        await operation() // Maintains caller's isolation
    }
}
```

#### SE-0434: Usability of Global-Actor-Isolated Types
Improvements for global actor usage:

```swift
// Sendable properties can be nonisolated
@MainActor
final class ViewModel: Sendable {
    // Good: Implicitly nonisolated (Sendable stored property)
    let id = UUID()

    // Bad: Must be isolated (non-Sendable)
    var items: [Item] = []

    // Good: Can be explicitly nonisolated
    nonisolated let configuration: Configuration
}

// Improved inference for closures
@MainActor
class Controller {
    func setup() {
        // Good: Closure inferred as @MainActor @Sendable
        Task {
            await updateData()
        }
    }
}
```

## Swift 6.1+ Roadmap

### Swift 6.1 (Shipped)

#### SE-0431: @isolated(any) Function Types
```swift
// Function types that preserve isolation
typealias IsolatedHandler = @isolated(any) () async -> Void

func withIsolation(_ handler: IsolatedHandler) async {
    await handler() // Maintains caller's isolation
}

// Use with actors
actor MyActor {
    func doWork() async {
        await withIsolation {
            // Runs on MyActor
            print("Isolated to: \(self)")
        }
    }
}
```

#### SE-0433: Synchronous Mutual Exclusion Lock (Mutex)
```swift
import Synchronization

// For protecting critical sections without async
final class Statistics: Sendable {
    private let mutex = Mutex<Stats>(.init())

    func record(value: Double) {
        mutex.withLock { stats in
            stats.count += 1
            stats.sum += value
        }
    }

    var average: Double {
        mutex.withLock { stats in
            stats.count > 0 ? stats.sum / Double(stats.count) : 0
        }
    }
}

private struct Stats {
    var count = 0
    var sum = 0.0
}
```

### Swift 6.2 (In Development)

#### SE-0461: Isolated Default Arguments
```swift
// Default values can use isolation context
@MainActor
class ViewModel {
    // Good: Default can access MainActor state
    func configure(
        title: String = defaultTitle // Coming in 6.2
    ) { }

    @MainActor
    static var defaultTitle: String { "Default" }
}
```

### Future Proposals Under Review

1. **SE-0449**: Allow nonisolated to prevent global actor inference
2. **SE-0450**: Limiting actor isolation inference
3. **SE-0451**: Isolated synchronous deinit
4. **Typed Throws in Concurrency**: Better error propagation
5. **Custom Executors v2**: More control over task execution

### Migration Timeline

| Version | Key Features | Migration Impact |
|---------|--------------|------------------|
| Swift 6.0 | Strict concurrency by default | Major - requires code updates |
| Swift 6.1 | Mutex, @isolated(any) | Minor - additive features |
| Swift 6.2 | Isolated defaults, deinit | Minor - quality of life |
| Swift 7.0 | Custom executors v2 | TBD - performance focused |

Stay updated: [Swift Evolution Dashboard](https://www.swift.org/swift-evolution/)

## Resources

### Official Documentation
- [Swift.org - Concurrency](https://docs.swift.org/swift-book/documentation/the-swift-programming-language/concurrency/)
- [Migrating to Swift 6](https://www.swift.org/migration/documentation/migrationguide/)
- [Data Race Safety](https://www.swift.org/migration/documentation/swift-6-concurrency-migration-guide/dataracesafety/)

### Swift Evolution Proposals
- [SE-0401: Remove Actor Isolation Inference](https://github.com/swiftlang/swift-evolution/blob/main/proposals/0401-remove-property-wrapper-isolation.md)
- [SE-0414: Region-based Isolation](https://github.com/swiftlang/swift-evolution/blob/main/proposals/0414-region-based-isolation.md)
- [SE-0420: Inheritance of Actor Isolation](https://github.com/swiftlang/swift-evolution/blob/main/proposals/0420-inheritance-of-actor-isolation.md)
- [SE-0430: Sending Parameter Values](https://github.com/swiftlang/swift-evolution/blob/main/proposals/0430-transferring-parameters-and-results.md)

### WWDC Sessions
- **WWDC 2024**: "Migrate your app to Swift 6" - Practical migration guide
- **WWDC 2022**: "Eliminate data races using Swift Concurrency" - Foundational concepts
- **WWDC 2021**: "Meet async/await in Swift" - Introduction to Swift concurrency

### Community Resources
- [Swift Forums - Concurrency](https://forums.swift.org/c/swift-evolution/concurrency/23)
- [Concurrency Index Thread](https://developer.apple.com/forums/thread/768776)
- [Swift Package Index](https://swiftpackageindex.com) - Shows "Safe from data races" badge
