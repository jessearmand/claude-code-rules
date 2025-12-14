# Performance Profiling with Instruments

Profile iOS/macOS applications using Xcode Instruments.

## Quick Start

```bash
# Open Instruments
open -a Instruments
```

## Profile from Command Line

### CPU Profiling (Time Profiler)

```bash
xcrun xctrace record \
    --template "Time Profiler" \
    --output profile.trace \
    --launch -- /path/to/app
```

### Memory Profiling (Allocations)

```bash
xcrun xctrace record \
    --template "Allocations" \
    --output memory.trace \
    --launch -- /path/to/app
```

### Attach to Running Process

```bash
xcrun xctrace record \
    --template "Time Profiler" \
    --output profile.trace \
    --attach $PID
```

## Available Templates

List all available templates:

```bash
xcrun xctrace list templates
```

Common templates:
- **Time Profiler** - CPU usage and call stacks
- **Allocations** - Memory allocations
- **Leaks** - Memory leaks detection
- **System Trace** - System-level performance
- **Network** - Network activity
- **Core Data** - Core Data performance
- **SwiftUI** - SwiftUI view updates

## Open Trace Files

```bash
open profile.trace
```

## Export Trace Data

Export to XML for analysis:

```bash
xcrun xctrace export \
    --input profile.trace \
    --output profile.xml
```

## Profile iOS Simulator App

```bash
# Get simulator app path
SIMULATOR_ID=$(xcrun simctl list devices booted -j | jq -r '.devices[][] | select(.state=="Booted") | .udid' | head -1)
APP_PATH=$(xcrun simctl get_app_container $SIMULATOR_ID com.example.app)

# Profile
xcrun xctrace record \
    --template "Time Profiler" \
    --device $SIMULATOR_ID \
    --output profile.trace \
    --launch -- $APP_PATH/MyApp.app/MyApp
```

## Build for Profiling

Build with Release configuration and debug symbols:

```bash
set -o pipefail && xcodebuild \
    -scheme $SCHEME \
    -configuration Release \
    -destination "platform=iOS Simulator,name=iPhone 16" \
    SWIFT_OPTIMIZATION_LEVEL=-O \
    DEBUG_INFORMATION_FORMAT=dwarf-with-dsym \
    build | xcbeautify
```

## Tips

- Profile **Release** builds for accurate performance data
- Use **Time Profiler** first to identify CPU bottlenecks
- Use **Allocations** to find memory issues
- Use **Leaks** periodically to catch retain cycles
- Profile on actual devices when possible for accurate metrics
