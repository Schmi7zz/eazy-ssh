# CHANGELOG — bot.py

## Chat Terminal Improvements

### New Features

- **VT100 Screen Buffer** — Proper rendering of full-screen terminal apps (nano, vim, htop, tmux, etc.)
  Handles cursor positioning, screen clearing, line erase, scrollback, and all common ANSI/VT100 escape sequences.

- **Context-Aware Glass Buttons** — The bot now shows relevant keyboard shortcuts based on what's running:
  - **nano**: Ctrl+O Save, Ctrl+X Exit, Ctrl+W Search, Ctrl+K Cut, Ctrl+U Paste, Ctrl+\ Replace
  - **nano exit prompt**: Y (Save) / N (Discard) / Ctrl+C Cancel
  - **nano filename prompt**: Enter (Confirm) / Ctrl+C Cancel
  - **vim**: :wq / :q! / i / Esc / :w / u
  - **tmux**: Detach, New, Prev/Next, List, Sessions, Copy, SplitH
  - **htop / top / less / python / mysql**: context-specific shortcuts

- **Nano State Machine** — Immediate context transitions when pressing buttons in nano
  (Ctrl+X → save prompt, Y → filename prompt, Enter → save & exit). No more waiting for screen output.

- **Raw Input Mode** — When inside editors (nano, vim, python, mysql), user text is sent as raw input
  so it lands in the editor buffer correctly instead of being executed as a shell command.

- **Log Mode Fixes** — New output is sent as separate messages, properly handles long content,
  empty chunks are skipped, memory capped at 5000 lines with auto-rotation.

- **Channel Membership Gate** *(optional)* — Restrict bot access to members of a Telegram channel.
  Configure via `REQUIRED_CHANNEL` env var (e.g. `@yourchannel`). If a user leaves the channel,
  their active session is disconnected within 20 seconds and they're prompted to rejoin.
  Bot must be an admin of the channel for membership checks to work.

### Bug Fixes

- **Enter key in editors** — Enter now sends `\r` (carriage return) instead of `\n` (line feed),
  matching how PTY raw mode expects input. Fixes Enter not working in nano's filename prompt.

- **Shutdown errors** — `import re` and `import time` moved to the top of the file to prevent
  `ImportError: sys.meta_path is None` during systemd stop/restart.

- **Graceful cleanup** — `_read_loop` and `_output_loop` now catch exceptions during shutdown
  so active sessions don't throw errors when the service is stopped.

### Config

New environment variables (all optional):

```bash
REQUIRED_CHANNEL=@yourchannel
REQUIRED_CHANNEL_URL=https://t.me/yourchannel
```

Leave empty to disable the membership gate.
