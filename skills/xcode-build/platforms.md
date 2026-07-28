# Platform Destinations

Configure `-destination` for different Apple platforms.

**Do not pass `-sdk` alongside `-destination`** — it is redundant and the two can contradict each
other. `-destination` alone is enough.

**Do not hardcode simulator names.** They change with every Xcode release (`iPhone 16` →
`iPhone 17`, `iPad Pro (12.9-inch)` → `iPad Pro 13-inch (M5)`, and so on) and vary per machine.
Query the installed set, then pass the UDID:

```bash
xcrun simctl list devices available          # all installed simulators
xcrun simctl list devices booted             # currently booted
-destination "platform=iOS Simulator,id=<UDID>"
```

Names are fine for throwaway commands you are about to run and read yourself; use `id=` in
anything scripted or reused.

## iOS Simulator

```bash
-destination "platform=iOS Simulator,id=$DEVICE_ID"
# or, by name, when you have just confirmed it exists:
-destination "platform=iOS Simulator,name=iPhone 17 Pro"
```

## iOS device

```bash
-destination "platform=iOS,id=<device-udid>"
-destination "generic/platform=iOS"          # build without a specific device attached
```

## macOS

```bash
-destination "platform=macOS"
-destination "platform=macOS,arch=arm64"     # pin the architecture
```

## visionOS / watchOS / tvOS Simulator

```bash
-destination "platform=visionOS Simulator,id=$DEVICE_ID"
-destination "platform=watchOS Simulator,id=$DEVICE_ID"
-destination "platform=tvOS Simulator,id=$DEVICE_ID"
```

These simulators are optional Xcode components and are frequently not installed. Confirm with
`xcrun simctl list devices available` before assuming a destination exists — a missing runtime
fails with an unhelpful "Unable to find a device matching the provided destination specifier".

## Resolving a UDID in a script

```bash
DEVICE_ID=$(xcrun simctl list devices available -j | python3 -c '
import json, sys
devices = json.load(sys.stdin)["devices"]
print(next(d["udid"] for runtime in devices.values() for d in runtime
           if "iPad" in d["name"]))')
```

Prefer a `booted` device when one exists, so builds and UI automation target the same simulator.

## Complete examples

### iOS test on a resolved simulator

```bash
set -o pipefail && xcodebuild \
    -project MyApp.xcodeproj \
    -scheme $SCHEME \
    -destination "platform=iOS Simulator,id=$DEVICE_ID" \
    test | xcbeautify
```

### macOS test

```bash
set -o pipefail && xcodebuild \
    -scheme $SCHEME \
    -destination "platform=macOS" \
    test | xcbeautify
```

### Multiple destinations in one invocation

```bash
set -o pipefail && xcodebuild \
    -scheme $SCHEME \
    -destination "platform=iOS Simulator,id=$IPHONE_ID" \
    -destination "platform=iOS Simulator,id=$IPAD_ID" \
    test | xcbeautify
```
