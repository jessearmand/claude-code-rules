# claude-compose.ps1 — "compose Claude Code prompt" helper for herdr AND psmux.
# PowerShell port of claude-compose.nu — keep the two in sync when editing either.
# Requires PowerShell 7+ (pwsh); also runs under Windows PowerShell 5.1.
#
# Launched by:
#   herdr  custom command:  cmd.exe /d /c "pwsh -NoProfile -File <this>"
#          (since upstream #1041 herdr runs Windows custom commands via cmd.exe,
#          NOT /bin/sh. Config: command = "pwsh -NoProfile -File C:/Users/SIEDrone/claude-compose.ps1")
#   psmux  prefix+C-e:      via claude-compose-launch.cmd (currently prefers nu;
#          add a `where pwsh` branch there if you standardize on PowerShell).
#
# Flow:
#   1. Edit ~/.claude-prompt.md in nvim (works around Claude Code's broken
#      Ctrl+G editor handover, issue #58664).
#   2. Inject the composed prompt into the ORIGINATING Claude pane:
#      - herdr: `herdr pane send-text` (server-side PTY write; bypasses herdr's
#        historical bracketed-paste INPUT freeze, upstream #858).
#      - psmux: `psmux send-paste` (base64 transport; psmux wraps in bracketed
#        paste itself when the app enabled DECSET 2004).
# See claude-compose.nu for the full rationale behind each step.
#
# Canonical copy lives in claude-code-rules/scripts/; the invocation path
# %USERPROFILE%\claude-compose.ps1 must hold the same content (re-copy after edits).

$ErrorActionPreference = 'Stop'
# Native (exe) nonzero exits should NOT throw — we check $LASTEXITCODE ourselves,
# mirroring nushell's `complete`. (No-op on Windows PowerShell 5.1.)
$PSNativeCommandUseErrorActionPreference = $false

$homeDir = if ($env:USERPROFILE) { $env:USERPROFILE } elseif ($env:HOME) { $env:HOME } else { '.' }
$prompt  = Join-Path $homeDir '.claude-prompt.md'
$logFile = Join-Path $homeDir '.claude-compose.log'

function Write-Log([string]$msg) {
    $ts = (Get-Date).ToString('yyyy-MM-dd HH:mm:ss')
    Add-Content -LiteralPath $logFile -Value "$ts $msg"
}

# Resolve nvim to a concrete program: pin the known install, else PATH, else bare
# name (a herdr custom pane may inherit a PATH without Neovim's dir).
$nvim = @('C:/Program Files/Neovim/bin/nvim.exe') +
        @(Get-Command nvim -ErrorAction SilentlyContinue | ForEach-Object { $_.Source }) |
        Where-Object { $_ -and (Test-Path -LiteralPath $_) } |
        Select-Object -First 1
if (-not $nvim) { $nvim = 'nvim' }

# Backend detection. HERDR_ACTIVE_PANE_CWD / TMUX_PANE are set FRESH per launch;
# HERDR_SOCKET_PATH alone is not a reliable discriminator (it leaks into nested
# shells).
$backend =
    if     ($env:HERDR_ACTIVE_PANE_CWD) { 'herdr' }
    elseif ($env:TMUX_PANE)             { 'psmux' }
    elseif ($env:HERDR_SOCKET_PATH)     { 'herdr' }
    else                                { '' }

$socketDisp = if ($env:HERDR_SOCKET_PATH)     { $env:HERDR_SOCKET_PATH }     else { '<unset>' }
$cwdDisp    = if ($env:HERDR_ACTIVE_PANE_CWD) { $env:HERDR_ACTIVE_PANE_CWD } else { '<unset>' }
$tmuxDisp   = if ($env:TMUX_PANE)             { $env:TMUX_PANE }             else { '<unset>' }
Write-Log ("start: backend={0} nvim={1} socket={2} active_cwd={3} tmux_pane={4}" -f `
    $backend, $nvim, $socketDisp, $cwdDisp, $tmuxDisp)

if (-not $backend) {
    Write-Log 'neither HERDR_SOCKET_PATH nor TMUX_PANE set; not inside herdr/psmux'
    exit 1
}

# 1) compose in nvim (blocks until the editor exits)
try {
    & $nvim -c 'setlocal fileformat=unix nofixeol noeol' $prompt
} catch {
    Write-Log ("nvim failed: {0}" -f $_.Exception.Message)
    exit 1
}

# 2) read the composed text
$text = if (Test-Path -LiteralPath $prompt) { [System.IO.File]::ReadAllText($prompt) } else { '' }
if ([string]::IsNullOrWhiteSpace($text)) {
    Write-Log 'empty prompt; nothing sent'
    exit 0
}

# 3) find the originating Claude pane and inject the text
if ($backend -eq 'herdr') {
    $targetCwd = $env:HERDR_ACTIVE_PANE_CWD
    $listed = herdr pane list
    if ($LASTEXITCODE -ne 0) {
        Write-Log ("pane list failed exit={0}" -f $LASTEXITCODE)
        exit 1
    }

    $panes = ($listed | ConvertFrom-Json).result.panes
    $match = $panes | Where-Object { $_.agent -eq 'claude' -and $_.cwd -eq $targetCwd }
    if (-not $match) {
        Write-Log ("no claude pane for cwd={0}; panes={1}" -f $targetCwd, ($panes | ConvertTo-Json -Compress))
        exit 1
    }
    $target = (@($match)[0]).pane_id

    # Wrap in bracketed-paste markers ourselves (`pane send-text` writes RAW,
    # unlike interactive paste). Normalize newlines to bare CR (\r): a real
    # terminal paste delivers \r, and herdr encodes Enter as \r too; the composed
    # file is LF. This makes the injection a proper terminal-style paste.
    $esc     = [char]27
    $textCr  = $text.Replace("`r`n", "`r").Replace("`n", "`r")
    $payload = "${esc}[200~${textCr}${esc}[201~"

    # "paste again to expand": the FIRST paste of some content only produces a
    # collapsed [Pasted text] placeholder; an identical SECOND paste expands it.
    # A single injection stays collapsed forever, so send the identical payload
    # TWICE (byte-identical) — the second matches the first and expands it. They
    # must land as separate paste events, hence the gap.
    herdr pane send-text $target $payload | Out-Null
    Start-Sleep -Milliseconds 120
    herdr pane send-text $target $payload | Out-Null
    Write-Log ("herdr send-text x2 -> {0} exit={1} len={2}" -f $target, $LASTEXITCODE, $text.Length)
} else {
    # psmux: this script's pane is the split; the originating (Claude) pane is the
    # other pane of the current window.
    $selfPane = $env:TMUX_PANE
    $listed = psmux list-panes
    if ($LASTEXITCODE -ne 0) {
        Write-Log ("psmux list-panes failed exit={0}" -f $LASTEXITCODE)
        exit 1
    }

    $candidates = [regex]::Matches((@($listed) -join "`n"), '%\d+') |
        ForEach-Object { $_.Value } |
        Where-Object { $_ -ne $selfPane } |
        Select-Object -Unique
    if (-not $candidates) {
        Write-Log ("no other pane in window; panes={0}" -f ((@($listed) -join ' ')))
        exit 1
    }
    if (@($candidates).Count -gt 1) {
        Write-Log ("multiple candidate panes {0}; using first" -f (@($candidates) -join ','))
    }
    $target = @($candidates)[0]

    # send-paste takes base64 (newline/quote-safe through the control protocol);
    # psmux adds the bracketed-paste markers itself when the app enabled DECSET 2004.
    # Same two fixes as the herdr branch: normalize newlines to bare CR (psmux
    # writes the decoded bytes as-is, and the file is LF), and send the identical
    # paste TWICE (~120ms apart) so Claude Code expands it instead of leaving the
    # first paste collapsed as a [Pasted text] placeholder.
    $textCr  = $text.Replace("`r`n", "`r").Replace("`n", "`r")
    $payload = [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($textCr))
    psmux send-paste -t $target $payload | Out-Null
    Start-Sleep -Milliseconds 120
    psmux send-paste -t $target $payload | Out-Null
    Write-Log ("psmux send-paste x2 -> {0} exit={1} len={2}" -f $target, $LASTEXITCODE, $text.Length)
}
