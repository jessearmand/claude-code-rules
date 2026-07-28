---
name: xcode-build
description: Build, test, and run iOS/macOS/visionOS projects using the Xcode MCP server or xcodebuild. Use when working with Xcode projects, Swift packages, or Apple platform development.
---

# Xcode Build & Test

## Pick the tool first

**Project open in Xcode → use the `xcode` MCP server** (`XcodeListWindows` → `BuildProject` /
`RunSomeTests`). It builds the scheme, configuration, and destination Xcode has selected, so the
product lands where the app you are testing comes from, and results come back structured.

**Otherwise (CI, headless, deliberate overrides) → `xcodebuild`,** always piped through
`xcbeautify`:

```bash
set -o pipefail && xcodebuild [flags] | xcbeautify
```

See [Xcode MCP vs. xcodebuild](xcode-mcp.md) for the tool map, the configuration trap that
silently installs stale binaries, and how to verify which product you are about to run.

## Two flags that cause most wasted time

- **`-configuration`** overrides the scheme's configuration. If the project defines custom
  configurations (`Debug-dev`, `Debug-staging`, …), passing `-configuration Debug` builds a
  *different* product into a *different* directory than the scheme would. Omit the flag and let
  the scheme decide unless you mean to override it.
- **`-sdk`** is unnecessary when you pass `-destination`, and the two can contradict each other.
  Use `-destination` alone.

## Common commands

| Task | Command |
|------|---------|
| List schemes | `xcodebuild -list -project MyApp.xcodeproj` |
| Show product paths | `xcodebuild -showBuildSettings -scheme $SCHEME \| rg BUILT_PRODUCTS_DIR` |
| List simulators | `xcrun simctl list devices available` |
| Build | `xcodebuild -project MyApp.xcodeproj -scheme $SCHEME build` |
| Test | `xcodebuild -project MyApp.xcodeproj -scheme $SCHEME test` |
| Test plan | `xcodebuild -scheme $SCHEME -testPlan UnitTests test` |
| Clean | `xcodebuild clean -project MyApp.xcodeproj` |

Use `-project MyApp.xcodeproj` for a project and `-workspace MyApp.xcworkspace` for a workspace.
Do not point `-workspace` at `MyApp.xcodeproj/project.xcworkspace` — that is a project's
implicit workspace, not a real one, and it resolves dependencies differently.

## Build for iOS Simulator

Simulator names change with every Xcode release, so resolve a UDID at runtime and pass `id=`
instead of hardcoding a device name:

```bash
DEVICE_ID=$(xcrun simctl list devices available -j \
  | python3 -c 'import json,sys; d=json.load(sys.stdin)["devices"];
print(next(x["udid"] for v in d.values() for x in v if "iPhone" in x["name"]))')

set -o pipefail && xcodebuild \
    -project $PROJECT_NAME.xcodeproj \
    -scheme $SCHEME \
    -destination "platform=iOS Simulator,id=$DEVICE_ID" \
    build | xcbeautify
```

Write the full log to a file when you need to inspect it — `| tail -N` drops the compile lines
that tell you whether your file was actually rebuilt:

```bash
set -o pipefail && xcodebuild … | xcbeautify > build.log; echo "exit=$?"
rg 'Compiling MyFile|error:' build.log
```

## Install and run on a simulator

```bash
APP=$(xcodebuild -project MyApp.xcodeproj -scheme $SCHEME -showBuildSettings \
        | rg -m1 'BUILT_PRODUCTS_DIR' | sed 's/.*= //')/MyApp.app
BUNDLE_ID=$(/usr/libexec/PlistBuddy -c 'Print CFBundleIdentifier' "$APP/Info.plist")

xcrun simctl terminate $DEVICE_ID "$BUNDLE_ID" 2>/dev/null
xcrun simctl install $DEVICE_ID "$APP"
xcrun simctl launch $DEVICE_ID "$BUNDLE_ID"
```

Note the `.app` name need not match the scheme or product name — list the directory rather than
assuming (`ls -d "$BUILT_PRODUCTS_DIR"/*.app`).

## Swift packages

Scheme names differ between a package opened directly and one embedded in a workspace; confirm
with `xcodebuild -list` rather than assuming a suffix. See [Swift Package Manager](swiftpm.md).

## Platform-specific guides

- [Xcode MCP vs. xcodebuild](xcode-mcp.md) — tool selection, configuration traps, verifying products
- [Platform destinations](platforms.md) — iOS, macOS, visionOS, watchOS, tvOS
- [Swift Package Manager](swiftpm.md) — SwiftPM and workspace integration
- [Profiling](profiling.md) — Instruments and `xctrace`
