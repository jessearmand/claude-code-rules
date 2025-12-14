# Swift Implementation Patterns

Common patterns for SwiftUI development.

## State Ownership

- Views own local state unless sharing is required
- State flows down, actions flow up
- Keep state close to where it's used
- Extract shared state only when multiple views need it

## Shared State with @Observable

```swift
@Observable
class UserSession {
    var isAuthenticated = false
    var currentUser: User?

    func signIn(user: User) {
        currentUser = user
        isAuthenticated = true
    }
}

struct MyApp: App {
    @State private var session = UserSession()

    var body: some Scene {
        WindowGroup {
            ContentView()
                .environment(session)
        }
    }
}
```

## Async Data Loading

```swift
struct ProfileView: View {
    @State private var profile: Profile?
    @State private var isLoading = false
    @State private var error: Error?

    var body: some View {
        Group {
            if isLoading {
                ProgressView()
            } else if let profile {
                ProfileContent(profile: profile)
            } else if let error {
                ErrorView(error: error)
            }
        }
        .task {
            await loadProfile()
        }
    }

    private func loadProfile() async {
        isLoading = true
        defer { isLoading = false }

        do {
            profile = try await ProfileService.fetch()
        } catch {
            self.error = error
        }
    }
}
```

## Async Patterns

- Use `async/await` as default for async operations
- Leverage `.task` modifier for lifecycle-aware work
- Avoid Combine unless absolutely necessary
- Handle errors with `try/catch`

## View Composition

- Build UI with small, focused views
- Extract reusable components naturally
- Use view modifiers to encapsulate styling
- Prefer composition over inheritance

## Code Organization

- Organize by feature, not by type
- Avoid `Views/`, `Models/`, `ViewModels/` folders
- Keep related code together
- Use extensions to organize large files

## Testing Strategy

- Unit test business logic and data transformations
- Use SwiftUI Previews for visual testing
- Test `@Observable` classes independently
- Keep tests simple and focused
- Don't sacrifice clarity for testability
