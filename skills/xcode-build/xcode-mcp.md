# Xcode MCP vs. plain `xcodebuild`

**When the project is already open in Xcode, build and test through the `xcode` MCP server.**
Reach for `xcodebuild` when Xcode is not open, or when you deliberately need settings that
differ from what Xcode has selected.

## The failure this prevents

`xcodebuild -configuration Debug` looks harmless, but it **overrides the scheme's
configuration**. Projects with custom configurations (`Debug-dev`, `Debug-staging`, …) put
their products in a per-configuration directory:

```
Build/Products/Debug-dev-iphonesimulator/SCEApp.app   ← what the scheme (and Xcode) builds
Build/Products/Debug-iphonesimulator/SCEApp.app       ← what `-configuration Debug` builds
```

Both builds "succeed". If you then install from the wrong directory, the simulator runs a
**stale binary** — often another build of the same bundle id, so nothing looks wrong. You end
up debugging a change that was never running. The MCP path avoids this entirely because it
builds exactly what Xcode is set to build.

## Tool map

| Tool | Use |
|---|---|
| `XcodeListWindows` | First call — returns `tabIdentifier` + `workspacePath`; every other tool needs the identifier |
| `BuildProject` | Build the selected scheme; returns `{buildResult, elapsedTime, errors[], fullLogPath}` |
| `GetBuildLog` | Query the last/current build; filter by `severity` (`error`/`warning`/`remark`), `pattern` (regex), `glob`; also reports whether a build is in progress |
| `GetTestList` / `RunAllTests` / `RunSomeTests` | Enumerate and run tests, including a subset |
| `XcodeListNavigatorIssues`, `XcodeRefreshCodeIssuesInFile` | Read the issues Xcode itself is showing |
| `RunCodeSnippet`, `RenderPreview`, `DocumentationSearch` | Evaluate snippets, render SwiftUI previews, search docs |
| `XcodeRead/Write/Update/Glob/Grep/LS/MV/RM/MakeDir` | File operations routed through Xcode |

## Why prefer it

1. **Scheme fidelity.** Scheme, configuration, and destination come from Xcode, so the product
   lands where the app you are testing actually comes from. No configuration/destination flags
   to get wrong.
2. **Structured results.** `errors[]` and `fullLogPath` come back as data — no `xcbeautify`
   parsing, and no chance of truncating the interesting part. (Piping a build through
   `| tail -N` is how compile lines get lost: a 40-line tail of a large build shows only the
   final link/copy steps, which makes a skipped recompile invisible.)
3. **Queryable logs after the fact.** `GetBuildLog` filters by severity, regex, or file glob,
   so you can ask "did it compile this file?" or "any warnings in this module?" without
   re-running anything.
4. **Shared incremental state.** It uses Xcode's build system and DerivedData. Running a
   separate `xcodebuild` against the same DerivedData makes the two reconcile each other's
   state — expect "Removed stale file" churn and, worse, targets being considered up to date
   when they are not.
5. **Test subsetting without string-building.** `GetTestList` + `RunSomeTests` beats composing
   `-only-testing:Target/Class/method` by hand.

## When plain `xcodebuild` is still right

- CI or any headless run — no Xcode GUI.
- A deliberate override: different configuration, SDK, or destination than Xcode's selection.
- Matrix builds/tests across several destinations in a loop.
- Operations the MCP does not cover: `-list`, `-showBuildSettings`, `clean`,
  `archive` / `-exportArchive`, `-testPlan`, custom build-setting overrides.

Always pipe it through `xcbeautify` (`set -o pipefail && xcodebuild … | xcbeautify`), and write
the **full** log to a file rather than tailing it:

```bash
set -o pipefail && xcodebuild … | xcbeautify > build.log; echo "exit=$?"
rg 'Compiling MyFile|error:' build.log      # then query the file
```

## Verifying you are about to run what you just built

Cheap checks, in order of preference:

```bash
# 1. Ask the build system where products actually go
xcodebuild -project MyApp.xcodeproj -scheme 'My Scheme' -showBuildSettings \
  | rg -m2 'BUILT_PRODUCTS_DIR|CONFIGURATION_BUILD_DIR'

# 2. Compare timestamps of every candidate before installing
rg --files "$DERIVED_DATA/Build/Products" | rg 'MyApp\.app/MyApp$'

# 3. After installing, confirm the installed bundle matches the build
C=$(xcrun simctl get_app_container <UDID> <bundle-id> app)
shasum -a 1 "$C/MyApp" "$BUILT/MyApp.app/MyApp"
```

**Anti-pattern:** verifying "is my change in this binary?" by demangling symbols, e.g.
`nm binary | xargs -n1 xcrun swift-demangle`. That spawns one process per symbol — tens of
thousands of symbols means minutes to hours, and it answers a question the timestamp already
answered. If you genuinely need demangled names, pass them all to a **single**
`swift-demangle` invocation (it reads stdin). Better still: run the app and observe the
behavior — that is the verification you actually wanted.
