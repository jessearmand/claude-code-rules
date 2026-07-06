@echo off
REM claude-compose-launch.cmd -- psmux `prefix + C-e` split launcher.
REM
REM Prefer the nu compose script (claude-compose.nu): edit ~/.claude-prompt.md
REM in nvim, then auto-inject the prompt into the originating Claude pane via
REM `psmux send-paste` (bracketed paste, no manual Cmd+V). When nushell is not
REM installed, fall back to the legacy claude-edit.cmd flow: nvim edit + OSC 52
REM clipboard yank, then paste into Claude manually.
REM
REM Canonical copy lives in claude-code-rules/scripts/; the invocation path
REM %USERPROFILE%\claude-compose-launch.cmd must hold the same content.
where nu >nul 2>nul
if %ERRORLEVEL%==0 (
    nu "%USERPROFILE%\claude-compose.nu"
) else (
    call "%USERPROFILE%\claude-edit.cmd"
)
