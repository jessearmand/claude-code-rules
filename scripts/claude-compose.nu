# claude-compose.nu — "compose Claude Code prompt" helper for herdr AND psmux.
#
# Launched by:
#   herdr  custom command:  cmd.exe /d /c "nu <this>"
#          (since upstream #1041 herdr runs Windows custom commands via cmd.exe,
#          NOT /bin/sh; `nu` is found on PATH. Config: command = "nu <this>".)
#          A PowerShell port lives beside this as claude-compose.ps1.
#   psmux  prefix+C-e:      split-window -v "claude-compose-launch.cmd"
#          (the launcher runs this script when nu is installed, else falls
#          back to the legacy claude-edit.cmd nvim+OSC52 clipboard flow.)
#
# Flow:
#   1. Edit ~/.claude-prompt.md in nvim (works around Claude Code's broken
#      Ctrl+G editor handover, issue #58664).
#   2. Inject the composed prompt into the ORIGINATING Claude pane:
#      - herdr: `herdr pane send-text` — a SERVER-SIDE write to the pane's PTY,
#        so it bypasses herdr's bracketed-paste INPUT freeze (upstream bug
#        #858) — works locally AND over SSH, with no manual paste.
#      - psmux: `psmux send-paste -t <pane> <base64>` — psmux's paste
#        injection (WriteConsoleInputW), which wraps in bracketed-paste
#        markers itself when the app enabled DECSET 2004; base64 transport
#        keeps newlines/quotes intact through the control protocol.
#
# Originating-pane discovery:
#   herdr  exposes the focused pane's cwd as $env.HERDR_ACTIVE_PANE_CWD, which
#          we match against `herdr pane list` (agent == claude). herdr also
#          sets HERDR_SOCKET_PATH so the CLI targets the right session.
#   psmux  sets TMUX_PANE to the SPLIT's own pane id; the originating pane is
#          the other pane of the current window (`psmux list-panes` from
#          inside a pane lists the current window). With more than one other
#          pane the first is used (logged) — keep the compose window simple.
#
# Canonical copy lives in claude-code-rules/scripts/; the invocation path
# ~/claude-compose.nu must hold the same content (the hardlink gets severed
# by editors that replace-via-rename — re-copy after editing the canonical).

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

# Backend detection. HERDR_ACTIVE_PANE_CWD is set FRESH by herdr's custom
# command for this very launch, so it is the authoritative herdr signal;
# TMUX_PANE is set fresh by psmux for its split. HERDR_SOCKET_PATH alone is
# NOT trustworthy as a discriminator — it survives into any nested shell
# (e.g. a psmux server started from inside a herdr pane inherits it).
let backend = if ($env.HERDR_ACTIVE_PANE_CWD? | default "" | is-not-empty) {
    "herdr"
} else if ($env.TMUX_PANE? | default "" | is-not-empty) {
    "psmux"
} else if ($env.HERDR_SOCKET_PATH? | default "" | is-not-empty) {
    "herdr"
} else {
    ""
}

do $logit $"start: backend=($backend) nvim=($nvim) socket=($env.HERDR_SOCKET_PATH? | default '<unset>') active_cwd=($env.HERDR_ACTIVE_PANE_CWD? | default '<unset>') tmux_pane=($env.TMUX_PANE? | default '<unset>')"

if ($backend | is-empty) {
    do $logit "neither HERDR_SOCKET_PATH nor TMUX_PANE set; not inside herdr/psmux"
    exit 1
}

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
if $backend == "herdr" {
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

    # Wrap the text in bracketed-paste markers ourselves. `pane send-text`
    # writes the bytes RAW to the pane's PTY (unlike interactive paste /
    # `pane run`, which wrap when the app has DECSET 2004 on). Raw bursts
    # reach Claude Code re-chunked by the ConPTY input path into ~1KB pieces,
    # and its paste heuristic ingests each chunk as a separate fragment — the
    # composed prompt shows up truncated / split in the input box. Claude Code
    # keeps bracketed paste enabled at its prompt, so with the markers the
    # whole text lands as ONE atomic paste (newlines preserved, nothing
    # auto-submitted).
    # Normalize newlines to bare CR (\r): a real terminal paste delivers \r, and
    # herdr encodes Enter as \r too; the composed file is LF (\n). This makes the
    # injection a proper terminal-style paste (so Claude Code collapses it).
    let esc = (char -u '1b')
    let text_cr = ($text | str replace --all "\r\n" "\r" | str replace --all "\n" "\r")
    let payload = $"($esc)[200~($text_cr)($esc)[201~"

    # Claude Code's "paste again to expand": the FIRST paste of some content only
    # produces a collapsed [Pasted text] placeholder; pasting the SAME content a
    # second time is what expands it. A single injection therefore stays collapsed
    # forever, and a later Cmd+V doesn't match it (different bytes) so it makes a
    # new block instead. Send the identical payload TWICE — the two bursts are
    # byte-identical by construction, so the second matches the first and expands
    # it. They must land as separate paste events, hence the gap.
    herdr pane send-text $target $payload | complete
    sleep 120ms
    let res = (herdr pane send-text $target $payload | complete)
    do $logit $"herdr send-text x2 -> ($target) exit=($res.exit_code) len=($text | str length) err=($res.stderr)"
} else {
    # psmux: this script runs in the split pane created by the keybind, so
    # TMUX_PANE is the split itself; the originating (Claude) pane is the
    # other pane of the current window.
    let self_pane = $env.TMUX_PANE
    let listed = (psmux list-panes | complete)
    if $listed.exit_code != 0 {
        do $logit $"psmux list-panes failed exit=($listed.exit_code) err=($listed.stderr)"
        exit 1
    }

    let candidates = (
        $listed.stdout
        | parse --regex '(?<id>%\d+)'
        | get id
        | where {|id| $id != $self_pane }
    )
    if ($candidates | is-empty) {
        do $logit $"no other pane in window; panes=($listed.stdout | str replace -a (char nl) ' ')"
        exit 1
    }
    if ($candidates | length) > 1 {
        do $logit $"multiple candidate panes ($candidates | str join ','); using first"
    }
    let target = ($candidates | first)

    # `send-paste` takes base64 (newline/quote-safe through the control
    # protocol) and injects it as a paste: psmux itself adds the bracketed-
    # paste markers when the target app enabled DECSET 2004 (Claude Code
    # does), so the prompt lands as ONE atomic paste without auto-submit.
    # `-t` temp-focuses the target for the write and restores focus.
    #
    # Same two fixes as the herdr branch (Claude Code sees the same PTY input):
    #   - Normalize newlines to bare CR (\r): psmux writes the decoded bytes
    #     as-is (src/input.rs write_paste_chunked), and the composed file is LF,
    #     so without this the paste is not byte-identical to a real terminal paste.
    #   - Send the identical paste TWICE (~120ms apart): the first paste only
    #     collapses to a [Pasted text] placeholder; an identical second paste is
    #     what expands it. One injection stays collapsed forever.
    let text_cr = ($text | str replace --all "\r\n" "\r" | str replace --all "\n" "\r")
    let payload = ($text_cr | encode base64)
    psmux send-paste -t $target $payload | complete
    sleep 120ms
    let res = (psmux send-paste -t $target $payload | complete)
    do $logit $"psmux send-paste x2 -> ($target) exit=($res.exit_code) len=($text | str length) err=($res.stderr)"
}
