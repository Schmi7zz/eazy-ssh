<div align="center">

  ███████╗ █████╗ ███████╗██╗   ██╗    ███████╗███████╗██╗  ██╗
  ██╔════╝██╔══██╗╚══███╔╝╚██╗ ██╔╝    ██╔════╝██╔════╝██║  ██║
  █████╗  ███████║  ███╔╝  ╚████╔╝     ███████╗███████╗███████║
  ██╔══╝  ██╔══██║ ███╔╝    ╚██╔╝      ╚════██║╚════██║██╔══██║
  ███████╗██║  ██║███████╗   ██║       ███████║███████║██║  ██║
  ╚══════╝╚═╝  ╚═╝╚══════╝   ╚═╝       ╚══════╝╚══════╝╚═╝  ╚═╝

<br /><br />

**SSH into your servers — right from Telegram.**

A full-featured SSH terminal as a Telegram Mini App.
No apps to install. No clients to configure. Just open and connect.

[![MIT License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Go](https://img.shields.io/badge/backend-Go-00ADD8.svg)](backend/)
[![Telegram](https://img.shields.io/badge/platform-Telegram-26A5E4.svg)](https://telegram.org)

[Features](#-features) · [Install](#-installation) · [Manual Setup](#-manual-setup) · [Telegram Channel](https://t.me/SchmitzWS)

**[🇮🇷 مستندات فارسی](README.fa.md)**

</div>

---

## ✨ Features

- 🖥 **Real terminal** — Full xterm.js with color, scrollback, and auto-resize
- 🔐 **Dual auth** — Password or SSH key (with passphrase support)
- 📱 **Mobile-first** — Control bar with Ctrl+C/D/Z/L, Tab, Esc, arrows, copy/paste
- 💾 **Persistent servers** — Saved server list survives app restarts
- 🛡 **Telegram-only** — HMAC-SHA256 initData validation — no access outside Telegram
- 🎨 **Native feel** — Adapts to Telegram's dark/light theme automatically
- ⚡ **Fast** — Go WebSocket backend, zero-latency feel

## 🏗 Architecture

```
┌──────────────────┐     WebSocket (wss://)     ┌──────────────────┐     SSH (tcp/22)     ┌──────────────┐
│  Telegram App    │ ◄──────────────────────►   │  Go Backend      │ ◄──────────────────► │  Your Server │
│  (Mini App)      │     encrypted + validated   │  (WebSocket→SSH) │     standard SSH     │  (anywhere)  │
└──────────────────┘                             └──────────────────┘                      └──────────────┘
```

## 📋 Prerequisites

- A **VPS** with Ubuntu 22+ and a public IP
- A **domain name** (Telegram requires HTTPS)
- A **Telegram bot token** from [@BotFather](https://t.me/BotFather)

Docker will be installed automatically if not present.

## 🚀 Installation

### Step 1 — Create DNS Records

Create two A records pointing to your VPS IP:

```
ssh-terminal.yourdomain.com  →  YOUR_VPS_IP
ssh-api.yourdomain.com       →  YOUR_VPS_IP
```

> If using Cloudflare, disable the proxy (grey cloud) initially.

### Step 2 — Run the Installer

SSH into your server and run:

```bash
git clone https://github.com/Schmi7zz/eazy-ssh.git /opt/ssh-terminal
cd /opt/ssh-terminal
bash install.sh
```

The installer will ask you for:

| Prompt | Example | Where to get it |
|--------|---------|-----------------|
| Frontend subdomain | `ssh-terminal.example.com` | Your DNS setup from Step 1 |
| Backend subdomain | `ssh-api.example.com` | Your DNS setup from Step 1 |
| Bot token | `123456:ABC-DEF...` | [@BotFather](https://t.me/BotFather) → `/newbot` |
| Admin Telegram ID | `123456789` | [@userinfobot](https://t.me/userinfobot) |
| Bot username | `EazySSH_bot` | The username you chose in BotFather |
| Mini App short name | `terminal` | Any name you want (a-z, 0-9, _) |
| Email | `you@email.com` | For SSL certificate |

It will then automatically:

1. Install Nginx, Certbot, Docker, python-telegram-bot
2. Build and start the Go WebSocket backend
3. Configure Nginx reverse proxy
4. Get SSL certificates from Let's Encrypt
5. Patch all config files with your domains
6. Start the Telegram bot as a systemd service

### Step 3 — Configure BotFather

After the installer finishes, go to [@BotFather](https://t.me/BotFather):

**Set the menu button:**
1. `/setmenubutton` → select your bot
2. URL: `https://ssh-terminal.yourdomain.com`
3. Title: `Open Terminal`

**Create the Mini App:**
1. `/newapp` → select your bot
2. Title, description, photo (640×360)
3. Web App URL: `https://ssh-terminal.yourdomain.com`
4. Short name: the one you entered during install (e.g. `terminal`)

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

## 📖 Manual Setup

<details>
<summary>If you prefer to set things up manually instead of using <code>install.sh</code>, click here.</summary>

<br>

**1. Install dependencies:**
```bash
apt update && apt install -y nginx certbot python3-certbot-nginx python3-pip git
pip3 install python-telegram-bot --break-system-packages
```

**2. Clone and configure:**
```bash
git clone https://github.com/Schmi7zz/eazy-ssh.git /opt/ssh-terminal
cd /opt/ssh-terminal
cp .env.example .env
nano .env   # fill in BOT_TOKEN, WEBAPP_URL, ADMIN_ID, USERS_FILE
```

**3. Edit frontend:**
```bash
nano frontend/index.html
# Change WS_URL to: wss://ssh-api.yourdomain.com/ws
# Change Telegram link to: https://t.me/YOUR_BOT/YOUR_APP
```

**4. Build backend:**
```bash
docker compose up -d --build
curl http://localhost:8080/health   # should print: ok
```

**5. Configure Nginx:**
```bash
cp nginx.conf.example /etc/nginx/sites-available/ssh-terminal
nano /etc/nginx/sites-available/ssh-terminal   # replace yourdomain.com
ln -s /etc/nginx/sites-available/ssh-terminal /etc/nginx/sites-enabled/
nginx -t && systemctl reload nginx
```

**6. Get SSL:**
```bash
certbot --nginx -d ssh-terminal.yourdomain.com -d ssh-api.yourdomain.com
```

**7. Start bot:**
```bash
cp ssh-terminal-bot.service /etc/systemd/system/
systemctl daemon-reload && systemctl enable ssh-terminal-bot && systemctl start ssh-terminal-bot
```

</details>

## 📁 Project Structure

```
eazy-ssh/
├── backend/
│   ├── main.go              # Go WebSocket→SSH proxy with Telegram auth
│   ├── go.mod
│   └── Dockerfile
├── frontend/
│   └── index.html           # React Mini App (single file, CDN-loaded)
├── bot.py                   # Telegram bot (/start, /stats)
├── install.sh               # Interactive installer
├── docker-compose.yml
├── nginx.conf.example
├── ssh-terminal-bot.service
├── .env.example
├── LICENSE
├── README.md
└── README.fa.md
```

## 🔒 Security

- **Telegram validation** — Every WebSocket connection validates `initData` via HMAC-SHA256. No valid Telegram session = no access.
- **No server-side credential storage** — SSH credentials are sent per-connection, never persisted on the backend.
- **Client-side only** — Server list saved in localStorage within Telegram's webview.
- **HTTPS everywhere** — All traffic encrypted via TLS.
- **Origin restriction** — Optional `ALLOWED_ORIGIN` env var.

## 🤝 Contributing

Pull requests welcome! Open issues for bugs or feature requests.

## 📬 Contact

[![Telegram Channel](https://img.shields.io/badge/Telegram-@SchmitzWS-26A5E4?style=for-the-badge&logo=telegram)](https://t.me/SchmitzWS)

## 📄 License

[MIT](LICENSE) — use it, fork it, ship it.
