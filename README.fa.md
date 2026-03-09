<div align="center">

<img src="docs/banner.svg" alt="EazySSH" width="100%" />

<br /><br />

**از طریق تلگرام به سرورهات SSH بزن.**

یه ترمینال SSH کامل به شکل مینی‌اپ تلگرام.
نیازی به نصب اپ یا کلاینت نیست. فقط باز کن و وصل شو.

[![MIT License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Go](https://img.shields.io/badge/backend-Go-00ADD8.svg)](backend/)
[![Telegram](https://img.shields.io/badge/platform-Telegram-26A5E4.svg)](https://telegram.org)

[امکانات](#-امکانات) · [شروع سریع](#-شروع-سریع) · [آموزش کامل](#-آموزش-کامل-نصب) · [کانال تلگرام](https://t.me/SchmitzWS)

**[🇬🇧 English](README.md)**

</div>

---

## ✨ امکانات

- 🖥 **ترمینال واقعی** — ترمینال کامل xterm.js با رنگ، اسکرول و ریسایز خودکار
- 🔐 **احراز هویت دوگانه** — اتصال با پسورد یا کلید SSH (با پشتیبانی passphrase)
- 📱 **موبایل فرست** — نوار کنترل لمسی با Ctrl، Tab، Esc، فلش‌ها، کپی/پیست
- 💾 **ذخیره سرورها** — لیست سرورها بعد از بستن اپ هم باقی می‌مونه
- 🛡 **فقط تلگرام** — اعتبارسنجی `initData` تلگرام با HMAC-SHA256 — بدون دسترسی از بیرون
- 🎨 **ظاهر بومی** — تم تلگرام (تاریک/روشن) رو خودکار اعمال می‌کنه
- ⚡ **سریع** — بکند Go با WebSocket، بدون تاخیر محسوس

## 🏗 معماری

```
┌──────────────────┐     WebSocket (wss://)     ┌──────────────────┐     SSH (tcp/22)     ┌──────────────┐
│  تلگرام          │ ◄──────────────────────►   │  بکند Go         │ ◄──────────────────► │  سرور شما    │
│  (مینی‌اپ)        │     رمزنگاری + اعتبارسنجی  │  (WebSocket→SSH) │     SSH استاندارد    │  (هرجایی)     │
│                  │                             │                  │                      │              │
│  xterm.js        │                             │  بررسی initData  │                      │  Linux/BSD   │
│  React UI        │                             │  تخصیص PTY       │                      │  هر سیستمی   │
│  نوار کنترل      │                             │  انتقال استریم    │                      │              │
└──────────────────┘                             └──────────────────┘                      └──────────────┘
```

## 🚀 شروع سریع

نیاز دارید: یه VPS با Docker، یه دامنه، و یه بات تلگرام.

```bash
git clone https://github.com/Schmi7zz/eazy-ssh.git
cd eazy-ssh
cp .env.example .env
nano .env  # توکن بات رو وارد کن
docker compose up -d --build
```

## 📖 آموزش کامل نصب

### پیش‌نیازها

| چی | چرا |
|-----|------|
| VPS (اوبونتو ۲۲+) | هاست بکند و فرانت |
| Docker و Docker Compose | اجرای بکند Go |
| دامنه | برای HTTPS لازمه (تلگرام بدون SSL کار نمیکنه) |
| بات تلگرام | از [@BotFather](https://t.me/BotFather) بسازید |

### مرحله ۱ — رکورد DNS بساز

برو توی پنل DNS دامنت و دو رکورد A بساز که به IP سرورت اشاره کنن:

```
ssh-terminal.yourdomain.com  →  IP_سرور
ssh-api.yourdomain.com       →  IP_سرور
```

> اگه کلادفلر داری، فعلاً پروکسی رو خاموش کن (ابر خاکستری) تا SSL بگیری.

### مرحله ۲ — SSH بزن به سرورت

```bash
ssh root@IP_سرور
```

### مرحله ۳ — نصب وابستگی‌ها

```bash
apt update && apt install -y nginx certbot python3-certbot-nginx python3-pip git
pip3 install python-telegram-bot --break-system-packages
```

### مرحله ۴ — کلون و تنظیم

```bash
cd /opt
git clone https://github.com/YOUR_USERNAME/eazy-ssh.git ssh-terminal
cd ssh-terminal
cp .env.example .env
nano .env
```

فایل `.env` رو پر کن:

```env
BOT_TOKEN=123456:ABC-DEF...        # از @BotFather
WEBAPP_URL=https://ssh-terminal.yourdomain.com
ADMIN_ID=123456789                  # آیدی عددی تلگرامت (از @userinfobot بگیر)
USERS_FILE=/opt/ssh-terminal/users.json
```

### مرحله ۵ — تنظیم فرانت

فایل `frontend/index.html` رو ادیت کن و دو مقدار رو عوض کن:

```javascript
// آدرس WebSocket بکندت
const WS_URL = "wss://ssh-api.yourdomain.com/ws";
```

```html
<!-- لینک بات تلگرامت (برای صفحه Access Denied) -->
<a href="https://t.me/YOUR_BOT/YOUR_APP" ...>
```

### مرحله ۶ — بکند رو بالا بیار

```bash
docker compose up -d --build
```

تست:

```bash
curl http://localhost:8080/health
# باید بنویسه: ok
```

### مرحله ۷ — تنظیم Nginx

```bash
cp nginx.conf.example /etc/nginx/sites-available/ssh-terminal
nano /etc/nginx/sites-available/ssh-terminal
```

`yourdomain.com` رو با دامنه واقعیت عوض کن، بعد:

```bash
ln -s /etc/nginx/sites-available/ssh-terminal /etc/nginx/sites-enabled/
nginx -t
systemctl reload nginx
```

### مرحله ۸ — گواهی SSL بگیر

```bash
certbot --nginx -d ssh-terminal.yourdomain.com -d ssh-api.yourdomain.com
```

ایمیلت رو وارد کن و شرایط رو قبول کن. Certbot خودکار Nginx رو برای HTTPS تنظیم می‌کنه.

### مرحله ۹ — تست توی مرورگر

آدرس `https://ssh-terminal.yourdomain.com` رو باز کن — باید "Access Denied" نشون بده (درسته! فقط از داخل تلگرام کار می‌کنه).

### مرحله ۱۰ — تنظیم بات تلگرام

برو پیش [@BotFather](https://t.me/BotFather):

1. `/newbot` — بات رو بساز و توکن رو بگیر
2. `/setmenubutton` — بات رو انتخاب کن → URL: `https://ssh-terminal.yourdomain.com` → عنوان: `Open Terminal`
3. `/newapp` — بات رو انتخاب کن → عنوان، توضیح، عکس (640×360) → Web App URL: `https://ssh-terminal.yourdomain.com` → اسم کوتاه: `terminal`

### مرحله ۱۱ — بات رو استارت کن

```bash
cp ssh-terminal-bot.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable ssh-terminal-bot
systemctl start ssh-terminal-bot
```

تست:

```bash
systemctl status ssh-terminal-bot
```

### مرحله ۱۲ — تمام! 🎉

بات رو توی تلگرام باز کن → **Open Terminal** رو بزن → سرور اضافه کن → وصل شو!

لینک مینی‌اپت: `https://t.me/YOUR_BOT/terminal`

دستورات ادمین:
- `/start` — پیام خوش‌آمد با دکمه Open Terminal
- `/stats` — تعداد و لیست کاربران (فقط ادمین)

## 📁 ساختار پروژه

```
eazy-ssh/
├── backend/
│   ├── main.go              # پراکسی Go: WebSocket→SSH با احراز هویت تلگرام
│   ├── go.mod               # وابستگی‌های Go
│   └── Dockerfile           # بیلد Docker چند مرحله‌ای
├── frontend/
│   └── index.html           # مینی‌اپ React (تک فایل، CDN)
├── bot.py                   # بات تلگرام (/start, /stats)
├── docker-compose.yml       # تنظیم کانتینر بکند
├── nginx.conf.example       # قالب Nginx ریورس پراکسی
├── ssh-terminal-bot.service # سرویس systemd برای بات
├── .env.example             # قالب متغیرهای محیطی
├── LICENSE                  # MIT
├── README.md                # مستندات انگلیسی
└── README.fa.md             # مستندات فارسی (همین فایل)
```

## 🔒 امنیت

- **اعتبارسنجی تلگرام** — هر اتصال WebSocket با HMAC-SHA256 و توکن بات اعتبارسنجی میشه. بدون نشست معتبر تلگرام = بدون دسترسی.
- **بدون ذخیره اطلاعات سمت سرور** — اطلاعات SSH هر بار ارسال میشن و هرگز روی بکند ذخیره نمیشن.
- **ذخیره‌سازی سمت کلاینت** — لیست سرورها توی localStorage مرورگر (داخل وبویو تلگرام) ذخیره میشه. پسوردها فقط روی دستگاه کاربر هستن.
- **HTTPS همه‌جا** — تمام ترافیک با TLS رمزنگاری شده.
- **محدودیت Origin** — متغیر محیطی `ALLOWED_ORIGIN` اختیاری برای محدود کردن اتصالات WebSocket.

## 🤝 مشارکت

Pull request خوش‌آمده! برای باگ یا درخواست قابلیت جدید issue باز کنید.

## 📬 ارتباط

سوالی دارید؟ ایده‌ای؟ بیاید کانال:

[![Telegram Channel](https://img.shields.io/badge/Telegram-@SchmitzWS-26A5E4?style=for-the-badge&logo=telegram)](https://t.me/SchmitzWS)

## 📄 لایسنس

[MIT](LICENSE) — استفاده کن، فورک کن، شیپ کن.
