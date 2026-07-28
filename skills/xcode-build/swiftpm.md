# Swift Package Manager Integration

Build and test Swift packages with `swift` and with Xcode.

## Which tool

- **`swift build` / `swift test`** — fastest, but only for packages whose platforms include
  macOS. A package declaring `platforms: [.iOS(...)]` only will fail to build this way; it needs
  `xcodebuild` with an iOS destination.
- **`xcodebuild`** — required for iOS/tvOS/watchOS/visionOS destinations, and for packages
  consumed by an app target.
- **Xcode MCP** — when the package is open in Xcode, see [xcode-mcp.md](xcode-mcp.md).

## Scheme names: check, don't assume

Xcode generates an aggregate `<Package>-Package` scheme when a package is a **dependency in a
workspace**. A package opened directly usually exposes schemes named after its products/targets,
with no suffix. Confirm before scripting:

```bash
xcodebuild -list                 # in the package directory
xcodebuild -list -workspace MyApp.xcworkspace
```

## Build and test

```bash
swift build
swift test
swift build -c release

# On a simulator destination
set -o pipefail && xcodebuild \
    -scheme $SCHEME \
    -destination "platform=iOS Simulator,id=$DEVICE_ID" \
    test | xcbeautify
```

## Package inside a workspace

```bash
set -o pipefail && xcodebuild \
    -workspace MyApp.xcworkspace \
    -scheme $SCHEME \
    -destination "platform=iOS Simulator,id=$DEVICE_ID" \
    test | xcbeautify
```

## Local package overrides

To point a workspace at a local checkout instead of the pinned remote revision:

```bash
xcodebuild -workspace MyApp.xcworkspace -scheme $SCHEME \
    -packagePath ../MyLocalPackage build
```

Dragging the package folder into the workspace in Xcode does the same thing and survives across
builds. Remember that a local override changes what `Package.resolved` records — check
`git status` before committing.

## Dependency resolution

```bash
xcodebuild -resolvePackageDependencies -workspace MyApp.xcworkspace -scheme $SCHEME
swift package resolve
swift package update                     # updates Package.resolved
swift package reset                      # clears the local cache when resolution is wedged
```

Binary targets and git-lfs do not mix: SPM clones with `git clone --shared` from a bare mirror
that has no LFS endpoint, so LFS-backed files arrive as pointer text and the build fails on an
unreadable archive. Keep binaries as plain git objects or host them as remote `binaryTarget`
URLs with a checksum.

## Code coverage

```bash
set -o pipefail && xcodebuild \
    -scheme $SCHEME \
    -destination "platform=macOS" \
    -enableCodeCoverage YES \
    test | xcbeautify

swift test --enable-code-coverage
```
