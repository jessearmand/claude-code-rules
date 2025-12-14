# Platform Destinations

Configure `-destination` for different Apple platforms.

## iOS Simulator

```bash
-sdk iphonesimulator \
-destination "platform=iOS Simulator,name=iPhone 16"
```

Common device names: `iPhone 16`, `iPhone 16 Pro`, `iPhone 16 Pro Max`, `iPad Pro (12.9-inch)`

## macOS

```bash
-destination "platform=macOS,name=My Mac"
```

## visionOS Simulator

```bash
-sdk xrsimulator \
-destination "platform=visionOS Simulator,name=Apple Vision Pro"
```

## watchOS Simulator

```bash
-sdk watchsimulator \
-destination "platform=watchOS Simulator,name=Apple Watch Series 10 (46mm)"
```

## tvOS Simulator

```bash
-sdk appletvsimulator \
-destination "platform=tvOS Simulator,name=Apple TV 4K (3rd generation)"
```

## Finding Available Simulators

List all available simulators:

```bash
xcrun simctl list devices available
```

List booted simulators:

```bash
xcrun simctl list devices booted
```

## Complete Examples

### iOS Test

```bash
set -o pipefail && xcodebuild \
    -configuration Debug \
    -scheme $SCHEME-Package \
    -sdk iphonesimulator \
    -destination "platform=iOS Simulator,name=iPhone 16" \
    test | xcbeautify
```

### macOS Test

```bash
set -o pipefail && xcodebuild \
    -configuration Debug \
    -scheme $SCHEME-Package \
    -destination "platform=macOS,name=My Mac" \
    test | xcbeautify
```

### visionOS Test

```bash
set -o pipefail && xcodebuild \
    -configuration Debug \
    -scheme $SCHEME-Package \
    -sdk xrsimulator \
    -destination "platform=visionOS Simulator,name=Apple Vision Pro" \
    test | xcbeautify
```
