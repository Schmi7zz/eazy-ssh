<div align="center">

<br>

```
  ███████╗ █████╗ ███████╗██╗   ██╗    ███████╗███████╗██╗  ██╗
  ██╔════╝██╔══██╗╚══███╔╝╚██╗ ██╔╝    ██╔════╝██╔════╝██║  ██║
  █████╗  ███████║  ███╔╝  ╚████╔╝     ███████╗███████╗███████║
  ██╔══╝  ██╔══██║ ███╔╝    ╚██╔╝      ╚════██║╚════██║██╔══██║
  ███████╗██║  ██║███████╗   ██║       ███████║███████║██║  ██║
  ╚══════╝╚═╝  ╚═╝╚══════╝   ╚═╝       ╚══════╝╚══════╝╚═╝  ╚═╝
```

### 🖥️ SSH Terminal inside Telegram

**Connect to your Linux servers directly from Telegram Mini App.**<br>
No app install needed. Works on iOS, Android & Desktop.

<br>

[**Try it →**](https://t.me/EazySSH_bot/terminal)&nbsp;&nbsp;&nbsp;·&nbsp;&nbsp;&nbsp;[**Telegram Channel →**](https://t.me/SchmitzWS)&nbsp;&nbsp;&nbsp;·&nbsp;&nbsp;&nbsp;[**Report Bug →**](../../issues)

**[🇮🇷 مستندات فارسی](README.fa.md)**

<br>

---

<br>

<img src="https://img.shields.io/badge/Go-00ADD8?style=for-the-badge&logo=go&logoColor=white" alt="Go" />&nbsp;
<img src="https://img.shields.io/badge/React-20232A?style=for-the-badge&logo=react&logoColor=61DAFB" alt="React" />&nbsp;
<img src="https://img.shields.io/badge/Telegram-2CA5E0?style=for-the-badge&logo=telegram&logoColor=white" alt="Telegram" />&nbsp;
<img src="https://img.shields.io/badge/Docker-2496ED?style=for-the-badge&logo=docker&logoColor=white" alt="Docker" />&nbsp;
<img src="https://img.shields.io/badge/xterm.js-000000?style=for-the-badge" alt="xterm.js" />

<br>

</div>

<br>

## ✦ What is this?

Eazy SSH is a **Telegram Mini App** that gives you a full Linux terminal right inside Telegram. It connects to your servers via SSH through a secure WebSocket proxy — no third-party apps, no port forwarding headaches.

<br>

## ✦ Features

| | |
|---|---|
| 🔐 **Secure** | Telegram initData validation — only works inside Telegram |
| 🔑 **Flexible Auth** | Password or SSH Key (with passphrase support) |
| 📱 **Mobile-First** | Touch-friendly control bar: Ctrl+C, Tab, arrows, copy/paste |
| 💾 **Persistent** | Server list saved locally — survives app restarts |
| 🎨 **Native Feel** | Adapts to Telegram's theme (dark/light) automatically |
| ⚡ **Real Terminal** | Full xterm.js with 256-color, scrollback, resize |
| 🐳 **Easy Deploy** | Single `docker compose up` for the backend |

<br>

## ✦ Architecture

```
┌─────────────────┐     WebSocket (wss)      ┌──────────────────┐      SSH       ┌─────────────┐
│  Telegram App    │ ◄──────────────────────► │  Go Proxy Server │ ◄────────────► │ Your Server │
│  (Mini App UI)   │                          │  (backend)       │                │ (any Linux) │
└─────────────────┘                           └──────────────────┘                └─────────────┘
      xterm.js                                  gorilla/websocket                   OpenSSH
      React                                     x/crypto/ssh
```

<br>

## ✦ Quick Start

### Prerequisites

- A **VPS** with public IP (Hetzner, DigitalOcean, etc.)
- **Docker** installed
- A **domain** (for SSL — Telegram requires HTTPS)
- **Telegram Bot** token from [@BotFather](https://t.me/BotFather)

---

### Step 1 — DNS Setup

Create two A records pointing to your server IP:

| Type | Name | Value |
|------|------|-------|
| A | `ssh-app` | `YOUR_SERVER_IP` |
| A | `ssh-api` | `YOUR_SERVER_IP` |

> Example: `ssh-app.example.com` and `ssh-api.example.com`

---

### Step 2 — Clone & Configure

```bash
ssh root@YOUR_SERVER_IP

git clone https://github.com/YOUR_USERNAME/eazy-ssh.git /opt/ssh-terminal
cd /opt/ssh-terminal
```

Create your `.env` file:

```bash
cp .env.example .env
nano .env
```

Fill in your values:

```env
BOT_TOKEN=123456:ABC-DEF...
WEBAPP_URL=https://ssh-app.example.com
ADMIN_ID=your_telegram_id
ALLOWED_ORIGIN=https://ssh-app.example.com
```

> Get your Telegram ID from [@userinfobot](https://t.me/userinfobot)

---

### Step 3 — Configure Frontend

Edit the WebSocket URL in the frontend:

```bash
nano frontend/index.html
```

Find this line and replace with your API domain:

```javascript
const WS_URL = "wss://ssh-api.example.com/ws";
```

---

### Step 4 — Start Backend

```bash
docker compose up -d --build
```

Verify it's running:

```bash
curl http://localhost:8080/health
# Should output: ok
```

---

### Step 5 — Nginx & SSL

Install Nginx and Certbot:

```bash
apt update && apt install -y nginx certbot python3-certbot-nginx
```

Copy and edit the Nginx config:

```bash
cp nginx.conf.example /etc/nginx/sites-available/ssh-terminal
nano /etc/nginx/sites-available/ssh-terminal
```

Replace `YOUR_FRONTEND_DOMAIN` and `YOUR_API_DOMAIN` with your actual domains.

Enable and test:

```bash
ln -s /etc/nginx/sites-available/ssh-terminal /etc/nginx/sites-enabled/
nginx -t
systemctl reload nginx
```

Get SSL certificates:

```bash
certbot --nginx -d ssh-app.example.com -d ssh-api.example.com
```

---

### Step 6 — Setup Telegram Bot

Install the bot:

```bash
pip3 install -r bot/requirements.txt --break-system-packages
```

Setup as a service:

```bash
cp eazy-ssh-bot.service /etc/systemd/system/
nano /etc/systemd/system/eazy-ssh-bot.service  # adjust paths if needed
systemctl daemon-reload
systemctl enable eazy-ssh-bot
systemctl start eazy-ssh-bot
```

---

### Step 7 — Register Mini App

Talk to [@BotFather](https://t.me/BotFather):

1. `/setmenubutton` → select your bot → `https://ssh-app.example.com` → `Open Terminal`
2. `/newapp` → select your bot → fill in title, description, photo (640×360) → URL: `https://ssh-app.example.com` → short name: `terminal`

---

### Step 8 — Done! 🎉

Open your bot in Telegram and tap **Open Terminal** or visit `t.me/YourBot/terminal`.

<br>

## ✦ Project Structure

```
eazy-ssh/
├── backend/
│   ├── main.go          # WebSocket ↔ SSH proxy server
│   ├── go.mod           # Go dependencies
│   └── Dockerfile       # Container build
├── frontend/
│   └── index.html       # React Mini App (single file, no build step)
├── bot/
│   ├── bot.py           # Telegram bot with /start and /stats
│   └── requirements.txt
├── docker-compose.yml
├── nginx.conf.example
├── eazy-ssh-bot.service # systemd unit for the bot
├── .env.example
├── .gitignore
├── LICENSE
└── README.md
```

<br>

## ✦ Security

- **Telegram-only access**: Frontend blocks non-Telegram browsers. Backend validates `initData` using HMAC-SHA256 with your bot token — [Telegram docs](https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app).
- **No credentials stored server-side**: SSH passwords/keys are sent per-session and never persisted on the proxy.
- **Origin restriction**: `ALLOWED_ORIGIN` limits WebSocket connections to your domain only.
- **Server list**: Stored in browser's `localStorage` on the user's device only.

<br>

## ✦ Bot Commands

| Command | Description |
|---------|-------------|
| `/start` | Welcome message + Open Terminal button |
| `/stats` | User count & list (admin only) |

<br>

## ✦ Contributing

PRs welcome! Feel free to open issues for bugs or feature requests.

<br>

## ✦ License

[MIT](LICENSE) — use it, fork it, ship it.

<br>

---

<div align="center">

**Built by [Schmitz](https://t.me/SchmitzWS)** ⬡

[Telegram Channel](https://t.me/SchmitzWS) · [Report Issue](../../issues)

</div>
