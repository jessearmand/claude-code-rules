---
name: lang-swift
description: Write or review Swift and SwiftUI code while preserving the project's deployment targets, architecture, and concurrency model.
---

# Swift Development

Identify whether the task concerns a Swift package, Apple platform app, or
cross-platform tool. Follow the project's Swift version, language mode, deployment
targets, and existing frameworks. Do not assume every Swift project uses SwiftUI
or requires migration to the newest state-management or concurrency APIs.

## Implementation and concurrency

- Preserve established UIKit, AppKit, SwiftUI, Combine, and model-layer boundaries.
  Introduce abstractions or migrate frameworks only when the task benefits from it.
- Use structured concurrency where it fits the existing design. Respect actor
  isolation, cancellation, and ownership across asynchronous work.
- Diagnose isolation and Sendable errors against the actual compiler settings.
  Avoid unchecked conformances or blanket actor annotations just to silence errors.
- Keep UI state ownership explicit and handle loading, failure, and cancellation
  according to the feature's behavior.

For a concurrency issue, use the [concurrency index](references/concurrency.md)
and read only the relevant reference. Verify version-sensitive examples against
the project's compiler; do not enable a new language mode as an incidental fix.

## SwiftUI state

Choose APIs supported by the deployment target and existing model architecture.
`@Observable` is a macro, and `ObservableObject` is a protocol; neither is a
property wrapper named `@ObservableObject`.

| API | Role |
|-----|------|
| `@State` | View-owned state; can own an Observation model where supported |
| `@Binding` | Read/write access to state owned elsewhere |
| `@Observable` | Adds Observation tracking to a model type |
| `@Bindable` | Creates bindings to properties of an Observation model |
| `ObservableObject` and `@Published` | Publisher-based model observation |
| `@StateObject` | Owns an `ObservableObject` instance for a view's lifetime |
| `@ObservedObject` | Observes an `ObservableObject` supplied to the view |
| `@Environment` / `@EnvironmentObject` | Reads dependencies supplied through the matching environment mechanism |

Use [SwiftUI patterns](patterns.md) when deciding ownership or asynchronous view
behavior. Preserve an existing model/view-model layer when it serves a clear purpose.

## Validation and build routing

Use repository scripts, the pinned toolchain, and the existing test framework.
Run affected tests and appropriate formatting/build checks for the changed target.
Do not require an iOS simulator for a platform-independent package or a full app
build for an unrelated documentation edit.

For Xcode build, test, simulator, or profiling work, use the `xcode-build` skill
when available. If it is unavailable, use repository build instructions and the
installed tools' help rather than blocking or installing a skill. Confirm the
actual scheme, destination, configuration, and supported platform before running.

For logging changes, use [logging guidance](logging.md). Report validation results
and any platform, signing, or toolchain limitations.
