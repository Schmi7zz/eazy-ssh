```
  ███████╗ █████╗ ███████╗██╗   ██╗    ███████╗███████╗██╗  ██╗
  ██╔════╝██╔══██╗╚══███╔╝╚██╗ ██╔╝    ██╔════╝██╔════╝██║  ██║
  █████╗  ███████║  ███╔╝  ╚████╔╝     ███████╗███████╗███████║
  ██╔══╝  ██╔══██║ ███╔╝    ╚██╔╝      ╚════██║╚════██║██╔══██║
  ███████╗██║  ██║███████╗   ██║       ███████║███████║██║  ██║
  ╚══════╝╚═╝  ╚═╝╚══════╝   ╚═╝       ╚══════╝╚══════╝╚═╝  ╚═╝
```

### 🖥️ SSH Terminal inside Telegram

**Connect to your Linux servers directly from Telegram Mini App.**
No app install needed. Works on iOS, Android & Desktop.

&nbsp;

[**Try it →**](https://t.me/EazySSH_bot/terminal) · [**Telegram Channel →**](https://t.me/SchmitzWS) · [**Report Bug →**](https://github.com/Schmi7zz/eazy-ssh/issues) · [**فارسی 🇮🇷**](README.fa.md)

&nbsp;

---

&nbsp;

![Go](https://img.shields.io/badge/Go-00ADD8?style=for-the-badge&logo=go&logoColor=white)
![React](https://img.shields.io/badge/React-20232A?style=for-the-badge&logo=react&logoColor=61DAFB)
![Telegram](https://img.shields.io/badge/Telegram-2CA5E0?style=for-the-badge&logo=telegram&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-2496ED?style=for-the-badge&logo=docker&logoColor=white)
![xterm.js](https://img.shields.io/badge/xterm.js-000000?style=for-the-badge)

---

## ✨ Features

### 🖥 Web Terminal (Mini App)
- **Real terminal** — Full xterm.js with color, scrollback, and auto-resize
- **Dual auth** — Password or SSH key (with passphrase support)
- **Mobile-first** — Control bar with Ctrl+C/D/Z/L, Tab, Esc, arrows, copy/paste
- **Multiple themes** — Dark, Light, Dracula, Monokai
- **Persistent servers** — Saved server list survives app restarts
- **Telegram-only** — HMAC-SHA256 initData validation
- **Native feel** — Adapts to Telegram's dark/light theme automatically

### 📂 SFTP File Manager
- **Browse files** — Navigate your server filesystem visually
- **Upload / Download** — Transfer files directly from Telegram
- **Create folders** — Make new directories on the fly
- **Rename / Delete** — Full file management
- **📝 Built-in Code Editor** — Edit files directly on the server with Ace editor
  - Syntax highlighting for 40+ languages (Python, Go, JS, Bash, YAML, Docker, etc.)
  - 10 editor themes (Monokai, Dracula, Cobalt, etc.)
  - Undo/Redo, Search (Ctrl+F), Word Wrap
  - Save with Ctrl+S — writes back to server instantly
  - Line/column indicator, error & warning counter
  - Max 2MB file size for editing

### 📟 Chat Terminal (Inline SSH)
- **SSH in chat** — Run commands directly in Telegram chat, no Mini App needed
- **Live output** — Terminal output displayed as auto-updating message
- **Control buttons:**

| Button | Action |
|--------|--------|
| ⏎ Enter | Send newline |
| ⛔ Ctrl+C | Interrupt running process |
| ✂️ Ctrl+X | Send SIGQUIT |
| 📎 Ctrl+B | Send Ctrl+B (tmux prefix) |
| ⏹ Disconnect | Close SSH session |
| 🧹 Clear | Clear terminal output |

- **/disconnect** command — Quick disconnect from chat
- **Reconnect** — One-tap reconnect after disconnect
- **Server management** — Add, edit, delete servers from chat

### 🛠 Admin Tools
- **/stats** — View registered users
- **/broadcast** — Send message to all users (text, photo, video, document)

## 🏗 Architecture

```
┌──────────────────┐     WebSocket (wss://)     ┌──────────────────┐     SSH (tcp/22)     ┌──────────────┐
│  Telegram App    │ ◄──────────────────────►   │  Go Backend      │ ◄──────────────────► │  Your Server │
│  (Mini App)      │     encrypted + validated   │  (WebSocket→SSH) │     standard SSH     │  (anywhere)  │
└──────────────────┘                             └──────────────────┘                      └──────────────┘

┌──────────────────┐     Telegram Bot API       ┌──────────────────┐     SSH (asyncssh)   ┌──────────────┐
│  Telegram Chat   │ ◄──────────────────────►   │  Python Bot      │ ◄──────────────────► │  Your Server │
│  (Chat Terminal) │     inline messages         │  (bot.py)        │     async SSH        │  (anywhere)  │
└──────────────────┘                             └──────────────────┘                      └──────────────┘
```

## 📋 Prerequisites

- A **VPS** with Ubuntu 22+ and a public IP
- A **domain name** (Telegram requires HTTPS)
- A **Telegram bot token** from [@BotFather](https://t.me/BotFather)

Docker will be installed automatically if not present.

## 🚀 Quick Install

### Step 1 — Create DNS Records

Create two A records pointing to your VPS IP:

```
ssh-terminal.yourdomain.com  →  YOUR_VPS_IP
ssh-api.yourdomain.com       →  YOUR_VPS_IP
```

> If using Cloudflare, disable the proxy (grey cloud) initially.

### Step 2 — One-Line Install

```bash
bash <(curl -s https://raw.githubusercontent.com/Schmi7zz/eazy-ssh/main/install.sh)
```

The installer will ask for your domains, bot token, and admin ID, then automatically set up everything.

### Step 3 — Configure BotFather

After install, go to [@BotFather](https://t.me/BotFather):

1. `/setmenubutton` → select your bot → URL: `https://ssh-terminal.yourdomain.com` → Title: `Open Terminal`
2. `/newapp` → select your bot → Web App URL: `https://ssh-terminal.yourdomain.com` → Short name: `terminal`

### Step 4 — Done! 🎉

Open `t.me/YOUR_BOT/terminal` in Telegram → add a server → connect!

## 🔧 Management

```bash
# Backend logs
docker-compose -f /opt/ssh-terminal/docker-compose.yml logs -f

# Restart backend
docker-compose -f /opt/ssh-terminal/docker-compose.yml restart

# Bot logs
journalctl -u ssh-terminal-bot -f

# Edit config
nano /opt/ssh-terminal/.env

# User stats (in Telegram)
/stats
```

## 📁 Project Structure

```
eazy-ssh/
├── backend/
│   ├── main.go              # Go WebSocket→SSH proxy with Telegram auth + SFTP
│   ├── go.mod
│   └── Dockerfile
├── frontend/
│   └── index.html           # React Mini App (terminal + SFTP + code editor)
├── bot.py                   # Telegram bot (chat terminal + server management + admin)
├── install.sh               # One-line interactive installer
├── docker-compose.yml
├── nginx.conf.example
├── ssh-terminal-bot.service
├── env.example
├── LICENSE
├── README.md
└── README.fa.md
```

## 🔒 Security

- **Telegram validation** — Every WebSocket connection validates `initData` via HMAC-SHA256
- **No server-side credential storage** — SSH credentials are sent per-connection, never persisted
- **Client-side only** — Server list saved in localStorage within Telegram's webview
- **HTTPS everywhere** — All traffic encrypted via TLS
- **Origin restriction** — Optional `ALLOWED_ORIGIN` env var

## 🤝 Contributing

Pull requests welcome! Open issues for bugs or feature requests.

## 📬 Contact

[![Telegram Channel](https://img.shields.io/badge/Telegram-@SchmitzWS-26A5E4?style=for-the-badge&logo=telegram)](https://t.me/SchmitzWS)

## 📄 License

[MIT](LICENSE) — use it, fork it, ship it.
