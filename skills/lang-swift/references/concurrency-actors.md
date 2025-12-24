# Swift Concurrency: Actors and Isolation
This is part of the Swift concurrency reference split for progressive disclosure.
See `concurrency.md` for the index.

## Actor System

### Basic Actor Usage

```swift
actor DatabaseConnection {
    private var isConnected = false
    private var activeQueries = 0

    func connect() async throws {
        guard !isConnected else { return }
        // Connection logic...
        isConnected = true
    }

    func query(_ sql: String) async throws -> [Row] {
        activeQueries += 1
        defer { activeQueries -= 1 }

        // Query execution...
        return rows
    }

    // Synchronous access for immutable data
    nonisolated let connectionString: String

    // Computed property without state access
    nonisolated var description: String {
        "Database connection to \(connectionString)"
    }
}
```

### MainActor for UI Code

```swift
// Entire class on MainActor
@MainActor
final class LoginViewModel: ObservableObject {
    @Published private(set) var isLoading = false
    @Published private(set) var error: Error?

    func login(username: String, password: String) async {
        isLoading = true
        defer { isLoading = false }

        do {
            // This switches to background for network call
            let user = try await AuthService.shared.login(
                username: username,
                password: password
            )
            // Automatically back on MainActor
            navigateToHome(user: user)
        } catch {
            self.error = error
        }
    }

    // Can run on any thread
    nonisolated func validateEmail(_ email: String) -> Bool {
        // Email validation logic...
    }
}
```

### Custom Global Actors

```swift
// Define a global actor for database operations
@globalActor
actor DatabaseActor {
    static let shared = DatabaseActor()

    // Custom executor for integration
    nonisolated var unownedExecutor: UnownedSerialExecutor {
        DatabaseQueue.shared.unownedExecutor
    }
}

// Apply to types handling database operations
@DatabaseActor
class UserRepository {
    private var cache: [UUID: User] = [:]

    func findUser(id: UUID) async throws -> User {
        if let cached = cache[id] {
            return cached
        }

        let user = try await database.fetch(User.self, id: id)
        cache[id] = user
        return user
    }

    func saveUser(_ user: User) async throws {
        try await database.save(user)
        cache[user.id] = user
    }
}

// Mix different actors in one type
class DataCoordinator {
    @DatabaseActor
    private var userRepo = UserRepository()

    @MainActor
    private var viewModel = UserListViewModel()

    func refreshUsers() async {
        // Fetch on DatabaseActor
        let users = await userRepo.fetchAllUsers()

        // Update on MainActor
        await viewModel.update(users: users)
    }
}
```
