# SwiftUI implementation patterns

Use for SwiftUI ownership, composition, and asynchronous behavior. Preserve the
project's deployment targets and model architecture; these are decision criteria,
not a requirement to restructure existing views.

## State ownership

Place state where its lifetime and sharing requirements belong. A view can own
local state, while a feature model, application model, or injected service can own
longer-lived state. Sharing is not the only reason to separate a model from a view.

For Observation-based models, use `@Observable` and select ownership, environment,
and binding APIs according to the target platform. For publisher-based models,
retain `ObservableObject` with the appropriate `@StateObject`, `@ObservedObject`,
or `@EnvironmentObject` wrapper. Do not change model systems during unrelated work.

## Asynchronous loading

Choose view-lifecycle tasks, user-action tasks, or service-owned work according to
how long the operation should live. For view work, consider `.task` or `.task(id:)`
when loading should follow appearance or a changing input.

- Represent loading, success, and failure states deliberately; decide whether a
  refresh retains existing data or clears it.
- Treat cancellation as a lifecycle event when appropriate, not automatically as
  an error to display. Avoid letting an old request overwrite newer results.
- Keep UI updates within the required actor isolation. Do not assume an `async`
  function automatically runs expensive work off the main actor.
- Preserve Combine-based flows where their publishers and subscriptions fit the
  feature. Adopt async sequences or async/await when a change actually calls for it.

## Composition and validation

Extract views, modifiers, and model operations when they clarify responsibility
or support reuse. Keep the repository's file organization and testing framework.
Test state transitions, cancellation, and error behavior where affected. Previews
help inspect appearance but do not replace tests of behavior or platform integration.

## Sources

- [Managing model data](https://developer.apple.com/documentation/swiftui/managing-model-data-in-your-app)
- [Migrating observation models](https://developer.apple.com/documentation/swiftui/migrating-from-the-observable-object-protocol-to-the-observable-macro): use for an intentional migration, not routine edits.
