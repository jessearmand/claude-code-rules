# Swift Concurrency: Migration, Examples, and Best Practices
This is part of the Swift concurrency reference split for progressive disclosure.
See `concurrency.md` for the index.

## Migration Guide

### Step 1: Enable Warnings in Swift 5 Mode

```swift
// In Package.swift
.target(
    name: "MyApp",
    swiftSettings: [
        .enableUpcomingFeature("StrictConcurrency"),
        .enableUpcomingFeature("ExistentialAny"),
        .enableUpcomingFeature("ConciseMagicFile")
    ]
)

// Or in Xcode Build Settings
// Strict Concurrency Checking: Complete
// SWIFT_STRICT_CONCURRENCY = complete
```

### Step 2: Fix Global Variables

```swift
// Before
var sharedFormatter = DateFormatter()

// After - Option 1: Make immutable
let sharedFormatter: DateFormatter = {
    let formatter = DateFormatter()
    formatter.dateStyle = .short
    return formatter
}()

// After - Option 2: Add actor isolation
extension DateFormatter {
    @MainActor
    static let shared: DateFormatter = {
        let formatter = DateFormatter()
        formatter.dateStyle = .short
        return formatter
    }()
}

// After - Option 3: Use nonisolated(unsafe) for legacy code
nonisolated(unsafe) var legacyGlobal: LegacyType?
```

### Step 3: Add Sendable Conformances

```swift
// Make your model types Sendable
struct User: Codable, Sendable {
    let id: UUID
    let name: String
    let email: String
}

// For reference types, ensure immutability
final class Configuration: Sendable {
    let apiKey: String
    let baseURL: URL

    init(apiKey: String, baseURL: URL) {
        self.apiKey = apiKey
        self.baseURL = baseURL
    }
}
```

### Step 4: Isolate UI Code

```swift
// Before
class ViewModel: ObservableObject {
    @Published var items: [Item] = []

    func loadItems() {
        Task {
            items = await fetchItems() // Potential race
        }
    }
}

// After
@MainActor
final class ViewModel: ObservableObject {
    @Published private(set) var items: [Item] = []

    func loadItems() async {
        items = await fetchItems() // Safe on MainActor
    }
}
```

### Step 5: Handle Callbacks and Delegates

```swift
// Before - Unclear isolation
protocol DataDelegate: AnyObject {
    func dataDidUpdate(_ data: Data)
}

// After - Explicit isolation
@MainActor
protocol DataDelegate: AnyObject {
    func dataDidUpdate(_ data: Data)
}

// Or use async alternatives
protocol DataProvider {
    func fetchData() async throws -> Data
}
```

### Progressive Migration Strategy

1. **Start with leaf modules**: Begin with modules that have few dependencies
2. **Fix simple issues first**: Immutable globals, missing Sendable conformances
3. **Isolate UI layer**: Add @MainActor to view controllers and view models
4. **Address shared state**: Convert to actors or use synchronization
5. **Enable Swift 6 mode**: Once warnings are resolved

## Code Examples

### Example 1: Image Processing Pipeline

```swift
// Image processor using actors and Sendable
actor ImageProcessor {
    private let cache = ImageCache()

    func process(_ image: UIImage, filters: [Filter]) async throws -> UIImage {
        // Check cache
        let cacheKey = CacheKey(image: image, filters: filters)
        if let cached = await cache.get(cacheKey) {
            return cached
        }

        // Process image
        var result = image
        for filter in filters {
            result = try await filter.apply(to: result)
        }

        // Cache result
        await cache.set(cacheKey, image: result)
        return result
    }
}

// Sendable filter protocol
protocol Filter: Sendable {
    func apply(to image: UIImage) async throws -> UIImage
}

// Concrete filter implementation
struct BlurFilter: Filter {
    let radius: Double

    func apply(to image: UIImage) async throws -> UIImage {
        // Implementation using Core Image
        let ciImage = CIImage(image: image)!
        let filter = CIFilter.gaussianBlur()
        filter.inputImage = ciImage
        filter.radius = Float(radius)

        let context = CIContext()
        let output = filter.outputImage!
        let cgImage = context.createCGImage(output, from: output.extent)!

        return UIImage(cgImage: cgImage)
    }
}
```

### Example 2: Network Layer with Proper Isolation

```swift
// Network service with clear isolation boundaries
actor NetworkService {
    private let session: URLSession
    private let decoder = JSONDecoder()
    private var activeTasks: [UUID: URLSessionTask] = [:]

    init(configuration: URLSessionConfiguration = .default) {
        self.session = URLSession(configuration: configuration)
    }

    func fetch<T: Decodable & Sendable>(
        _ type: T.Type,
        from url: URL
    ) async throws -> T {
        let taskID = UUID()

        let task = session.dataTask(with: url)
        activeTasks[taskID] = task

        defer { activeTasks.removeValue(forKey: taskID) }

        let (data, response) = try await withTaskCancellationHandler {
            try await session.data(from: url)
        } onCancel: {
            task.cancel()
        }

        guard let httpResponse = response as? HTTPURLResponse,
              (200...299).contains(httpResponse.statusCode) else {
            throw NetworkError.invalidResponse
        }

        return try decoder.decode(type, from: data)
    }

    func cancelAll() {
        activeTasks.values.forEach { $0.cancel() }
        activeTasks.removeAll()
    }

    nonisolated var activeTaskCount: Int {
        get async { await activeTasks.count }
    }
}

// Usage with proper error handling
@MainActor
class UserListViewModel: ObservableObject {
    @Published private(set) var users: [User] = []
    @Published private(set) var isLoading = false
    @Published private(set) var error: Error?

    private let networkService = NetworkService()

    func loadUsers() async {
        isLoading = true
        error = nil

        do {
            let url = URL(string: "https://api.example.com/users")!
            users = try await networkService.fetch([User].self, from: url)
        } catch {
            self.error = error
        }

        isLoading = false
    }
}
```

### Example 3: Concurrent Data Processing

```swift
// Parallel processing with proper isolation
struct DataProcessor {
    func processFiles(_ urls: [URL]) async throws -> [ProcessedData] {
        try await withThrowingTaskGroup(of: ProcessedData.self) { group in
            // Add tasks for each file
            for url in urls {
                group.addTask {
                    try await processFile(url)
                }
            }

            // Collect results
            var results: [ProcessedData] = []
            for try await result in group {
                results.append(result)
            }

            return results
        }
    }

    private func processFile(_ url: URL) async throws -> ProcessedData {
        let data = try await readFile(url)
        let processed = try await transform(data)
        return ProcessedData(
            originalURL: url,
            processedData: processed,
            timestamp: Date()
        )
    }
}

// Result type that's Sendable
struct ProcessedData: Sendable {
    let originalURL: URL
    let processedData: Data
    let timestamp: Date
}
```

## Best Practices

### 1. Design for Sendability

```swift
// Bad: Avoid mutable reference types
class Settings {
    var theme: Theme
    var notifications: Bool
}

// Good: Prefer value types or immutable reference types
struct Settings: Sendable {
    let theme: Theme
    let notifications: Bool
}

// Good: Or use actors for mutable state
actor SettingsManager {
    private var settings: Settings

    func update(theme: Theme) {
        settings = Settings(
            theme: theme,
            notifications: settings.notifications
        )
    }
}
```

### 2. Minimize Actor Hops

```swift
// Bad: Excessive actor hopping
@MainActor
class ViewModel {
    func processData() async {
        let data = await dataActor.getData()
        let processed = await processorActor.process(data)
        let formatted = await formatterActor.format(processed)
        updateUI(formatted)
    }
}

// Good: Batch operations
@MainActor
class ViewModel {
    func processData() async {
        let result = await dataActor.getProcessedAndFormattedData()
        updateUI(result)
    }
}
```

### 3. Use nonisolated for Pure Functions

```swift
actor Calculator {
    private var history: [Calculation] = []

    // Good: Pure functions don't need isolation
    nonisolated func add(_ a: Double, _ b: Double) -> Double {
        a + b
    }

    nonisolated func multiply(_ a: Double, _ b: Double) -> Double {
        a * b
    }

    // State-modifying functions need isolation
    func recordCalculation(_ calc: Calculation) {
        history.append(calc)
    }
}
```

### 4. Leverage Structured Concurrency

```swift
// Good: Use task groups for parallel work
func downloadImages(urls: [URL]) async throws -> [UIImage] {
    try await withThrowingTaskGroup(of: (Int, UIImage).self) { group in
        for (index, url) in urls.enumerated() {
            group.addTask {
                let image = try await downloadImage(from: url)
                return (index, image)
            }
        }

        var images = Array<UIImage?>(repeating: nil, count: urls.count)
        for try await (index, image) in group {
            images[index] = image
        }

        return images.compactMap { $0 }
    }
}
```

### 5. Handle Cancellation Properly

```swift
func longRunningOperation() async throws -> Result {
    try await withTaskCancellationHandler {
        var progress = 0.0

        while progress < 1.0 {
            try Task.checkCancellation()

            // Do work...
            progress += 0.1

            try await Task.sleep(for: .seconds(1))
        }

        return result
    } onCancel: {
        // Cleanup resources
        cleanupOperation()
    }
}
```

## Performance Tuning

### Task Creation Overhead

```swift
// Bad: Excessive task creation
for item in items {
    Task {
        await process(item) // Creates N unstructured tasks
    }
}

// Good: Use TaskGroup for batch operations
await withTaskGroup(of: Void.self) { group in
    for item in items {
        group.addTask {
            await process(item) // Structured, limited concurrency
        }
    }
}

// Good: Or use concurrent forEach
await items.concurrentForEach { item in
    await process(item)
}
```

### Clock APIs for Efficient Timing

```swift
// Bad: Old-style sleep
Task {
    Thread.sleep(forTimeInterval: 1.0) // Blocks thread
}

// Bad: Task.sleep with nanoseconds
Task {
    try await Task.sleep(nanoseconds: 1_000_000_000)
}

// Good: Modern Clock-based approach
let clock = ContinuousClock()
try await clock.sleep(for: .seconds(1))

// Good: Measure elapsed time
let elapsed = await clock.measure {
    await expensiveOperation()
}
print("Operation took: \(elapsed)")

// Good: Custom clock for testing
struct TestClock: Clock {
    var now: Instant { .init() }
    func sleep(until deadline: Instant) async throws {
        // Instant return for tests
    }
}
```

### Structured vs Unstructured Tasks

```swift
// Bad: Unstructured tasks lose context
class Service {
    func startBackgroundWork() {
        Task {
            await longRunningWork() // No cancellation propagation
        }
    }
}

// Good: Structured tasks with proper lifecycle
class Service {
    private var workTask: Task<Void, Never>?

    func startBackgroundWork() {
        workTask = Task {
            try await withTaskCancellationHandler {
                await longRunningWork()
            } onCancel: {
                cleanup()
            }
        }
    }

    func stopWork() {
        workTask?.cancel()
    }
}

// Good: Detached tasks only for true daemons
Task.detached(priority: .background) {
    // Long-lived background monitoring
    while !Task.isCancelled {
        await checkSystemHealth()
        try await Task.sleep(for: .minutes(5))
    }
}
```

### Actor Contention Optimization

```swift
// Bad: High contention on single actor
actor Counter {
    private var value = 0

    func increment() {
        value += 1
    }
}

// Good: Reduce contention with batching
actor Counter {
    private var value = 0

    func increment(by amount: Int = 1) {
        value += amount
    }

    func batchIncrement(_ operations: [Int]) {
        value += operations.reduce(0, +)
    }
}

// Good: Or use sharding for high throughput
actor ShardedCounter {
    private var shards: [Int]

    init(shardCount: Int = ProcessInfo.processInfo.activeProcessorCount) {
        self.shards = Array(repeating: 0, count: shardCount)
    }

    func increment() {
        let shard = Int.random(in: 0..<shards.count)
        shards[shard] += 1
    }

    var total: Int {
        shards.reduce(0, +)
    }
}
```
