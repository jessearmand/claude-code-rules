# claude-compose.nu — herdr "compose Claude Code prompt" helper.
#
# Launched by herdr's custom command as:  /bin/sh -c "exec nu <this>"
# (herdr hardcodes /bin/sh for custom commands even on Windows; we provide
#  C:\bin\sh.exe. `nu` is found on PATH.)
#
# Flow:
#   1. Edit ~/.claude-prompt.md in nvim (works around Claude Code's broken
#      Ctrl+G editor handover, issue #58664).
#   2. Inject the composed prompt into the ORIGINATING Claude pane via herdr's
#      socket API (`herdr pane send-text`). That is a SERVER-SIDE write to the
#      pane's PTY, so it bypasses herdr's bracketed-paste INPUT freeze (upstream
#      bug #858) — works locally AND over SSH, with no manual paste.
#
# The originating pane is the one focused when the keybind was pressed; herdr
# exposes its cwd as $env.HERDR_ACTIVE_PANE_CWD, which we match against
# `herdr pane list`. herdr also sets HERDR_SOCKET_PATH so the CLI targets the
# right session.
#
# Canonical copy lives in claude-code-rules/scripts/; the invocation path
# ~/claude-compose.nu is a hardlink to this file (see claude-edit.cmd pattern).

let prompt = ($nu.home-path | path join ".claude-prompt.md")
let log_file = ($nu.home-path | path join ".claude-compose.log")
let logit = {|msg: string|
    $"(date now | format date '%Y-%m-%d %H:%M:%S') ($msg)\n" | save --append $log_file
}

# 1) compose in nvim (blocks until the editor exits)
^nvim -c 'setlocal fileformat=unix nofixeol noeol' $prompt

# 2) read the composed text
let text = (try { open --raw $prompt | decode utf-8 } catch { "" })
if ($text | str trim | is-empty) {
    do $logit "empty prompt; nothing sent"
    exit 0
}

# 3) find the originating Claude pane and inject the text server-side
let target_cwd = ($env.HERDR_ACTIVE_PANE_CWD? | default "")
let listed = (herdr pane list | complete)
if $listed.exit_code != 0 {
    do $logit $"pane list failed exit=($listed.exit_code) err=($listed.stderr)"
    exit 1
}

let panes = ($listed.stdout | from json | get result.panes)
let match = ($panes | where {|p| ($p.agent? == "claude") and ($p.cwd == $target_cwd) })
if ($match | is-empty) {
    do $logit $"no claude pane for cwd=($target_cwd); panes=($panes | to json -r)"
    exit 1
}

let target = ($match | first | get pane_id)
let res = (herdr pane send-text $target $text | complete)
do $logit $"send-text -> ($target) exit=($res.exit_code) len=($text | str length) err=($res.stderr)"
