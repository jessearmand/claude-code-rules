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

# nushell renamed `$nu.home-path` -> `$nu.home-dir`; fall back to USERPROFILE
# so this keeps working across nu versions (both nu installs on this machine
# expose `home-dir`). Using the old name threw column_not_found and made the
# pane close before anything could run or log.
let home = ($nu.home-dir? | default ($env.USERPROFILE? | default ($env.HOME? | default ".")))
let prompt = ($home | path join ".claude-prompt.md")
let log_file = ($home | path join ".claude-compose.log")
let logit = {|msg: string|
    $"(date now | format date '%Y-%m-%d %H:%M:%S') ($msg)\n" | save --append $log_file
}

# Resolve nvim to a concrete program. A herdr custom pane inherits herdr's
# launch PATH (apply_pane_base_env only injects HERDR_SOCKET_PATH), so if herdr
# was started from a shell whose PATH lacks Neovim's dir, a bare `nvim` would
# not resolve — and in nushell a failed external command aborts the script
# before we can log anything (the pane just flashes and closes). Pin the known
# install path when present, then fall back to PATH lookup, then bare name.
let nvim = (
    ["C:/Program Files/Neovim/bin/nvim.exe"]
    | append (which nvim | get path)
    | where {|p| $p | path exists }
    | append "nvim"
    | first
)

do $logit $"start: nvim=($nvim) socket=($env.HERDR_SOCKET_PATH? | default '<unset>') active_cwd=($env.HERDR_ACTIVE_PANE_CWD? | default '<unset>')"

# 1) compose in nvim (blocks until the editor exits). Guard the call so a
#    spawn/exit failure is logged instead of silently aborting the script.
try {
    ^$nvim -c 'setlocal fileformat=unix nofixeol noeol' $prompt
} catch {|err|
    do $logit $"nvim failed: ($err.msg)"
    exit 1
}

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

# Wrap the text in bracketed-paste markers ourselves. `pane send-text` writes
# the bytes RAW to the pane's PTY (unlike interactive paste / `pane run`, which
# wrap when the app has DECSET 2004 on). Raw bursts reach Claude Code re-chunked
# by the ConPTY input path into ~1KB pieces, and its paste heuristic ingests
# each chunk as a separate fragment — the composed prompt shows up truncated /
# split in the input box. Claude Code keeps bracketed paste enabled at its
# prompt, so with the markers the whole text lands as ONE atomic paste
# (newlines preserved, nothing auto-submitted).
let esc = (char -u '1b')
let payload = $"($esc)[200~($text)($esc)[201~"
let res = (herdr pane send-text $target $payload | complete)
do $logit $"send-text -> ($target) exit=($res.exit_code) len=($text | str length) err=($res.stderr)"
