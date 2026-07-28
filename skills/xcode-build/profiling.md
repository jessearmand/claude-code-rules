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

`get_app_container … app` already returns the `.app` bundle path — do not append the bundle name
again:

```bash
SIMULATOR_ID=$(xcrun simctl list devices booted -j \
  | python3 -c 'import json,sys; d=json.load(sys.stdin)["devices"];
print(next(x["udid"] for v in d.values() for x in v if x["state"]=="Booted"))')

APP_PATH=$(xcrun simctl get_app_container $SIMULATOR_ID com.example.app app)   # → …/MyApp.app

xcrun xctrace record \
    --template "Time Profiler" \
    --device $SIMULATOR_ID \
    --output profile.trace \
    --launch "$APP_PATH"
```

To profile an app that is already running, attach by pid instead — launching a second copy
through `xctrace` restarts it and loses the state you wanted to measure:

```bash
PID=$(xcrun simctl spawn $SIMULATOR_ID launchctl list | rg com.example.app | awk '{print $1}')
xcrun xctrace record --template "Time Profiler" --output profile.trace --attach $PID
```

Simulator numbers are indicative only — the app runs on the Mac's CPU with a different GPU and
memory system. Use the simulator to find algorithmic hot spots, and a real device for anything
you intend to quote.

## Build for Profiling

Build with Release configuration and debug symbols:

```bash
set -o pipefail && xcodebuild \
    -scheme $SCHEME \
    -configuration Release \
    -destination "platform=iOS Simulator,id=$SIMULATOR_ID" \
    DEBUG_INFORMATION_FORMAT=dwarf-with-dsym \
    build | xcbeautify
```

`-configuration Release` already sets `SWIFT_OPTIMIZATION_LEVEL=-O`; overriding it separately is
redundant. If the project uses custom configuration names, check `xcodebuild -list` for the
release-equivalent name (`Release-dev`, `Profile`, …) rather than assuming `Release` exists.

## Tips

- Profile **Release** builds for accurate performance data
- Use **Time Profiler** first to identify CPU bottlenecks
- Use **Allocations** to find memory issues
- Use **Leaks** periodically to catch retain cycles
- Profile on actual devices when possible for accurate metrics
