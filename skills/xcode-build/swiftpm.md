# Swift Package Manager Integration

Build and test Swift packages with Xcode.

## Package Scheme Naming

Swift packages use a `-Package` suffix for schemes:

```bash
# If your package is named "MyLibrary"
# The scheme is "MyLibrary-Package"
xcodebuild -scheme MyLibrary-Package test
```

## List Available Schemes

```bash
# In the package directory
xcodebuild -list
```

## Build Package

```bash
swift build
# or with xcodebuild
xcodebuild -scheme $SCHEME-Package build
```

## Test Package

```bash
swift test
# or with xcodebuild for specific platform
set -o pipefail && xcodebuild \
    -scheme $SCHEME-Package \
    -destination "platform=macOS,name=My Mac" \
    test | xcbeautify
```

## Package with Workspace

When a Swift package is part of an Xcode workspace:

```bash
set -o pipefail && xcodebuild \
    -workspace MyApp.xcworkspace \
    -scheme MyPackage-Package \
    -destination "platform=iOS Simulator,name=iPhone 16" \
    test | xcbeautify
```

## Multi-Platform Package Testing

Test the same package on multiple platforms:

```bash
# macOS
swift test

# iOS Simulator
set -o pipefail && xcodebuild \
    -scheme $SCHEME-Package \
    -sdk iphonesimulator \
    -destination "platform=iOS Simulator,name=iPhone 16" \
    test | xcbeautify

# visionOS Simulator
set -o pipefail && xcodebuild \
    -scheme $SCHEME-Package \
    -sdk xrsimulator \
    -destination "platform=visionOS Simulator,name=Apple Vision Pro" \
    test | xcbeautify
```

## Code Coverage

Generate code coverage for packages:

```bash
set -o pipefail && xcodebuild \
    -scheme $SCHEME-Package \
    -destination "platform=macOS,name=My Mac" \
    -enableCodeCoverage YES \
    test | xcbeautify
```

## Release Build

```bash
swift build -c release
```
