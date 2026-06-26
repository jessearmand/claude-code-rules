@echo off
REM Open the Claude Code prompt scratchpad in nvim (psmux `prefix + C-e` split).
REM Works around the broken Claude Code Ctrl+G editor handover (issue #58664):
REM Claude never spawns the editor. On `:wq`, an nvim BufWritePost autocmd yanks
REM the buffer to the system clipboard via OSC 52 (relayed by psmux to Ghostty ->
REM macOS clipboard), so you just Cmd+V into Claude. We do NOT use psmux's
REM paste-buffer here: its buffer store strips single-quotes and escapes newlines.
REM
REM `-c "setlocal fileformat=unix nofixeol noeol"` keeps the saved file clean.
nvim -c "setlocal fileformat=unix nofixeol noeol" "%USERPROFILE%\.claude-prompt.md"
