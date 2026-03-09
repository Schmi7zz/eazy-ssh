#!/bin/bash
# ╔══════════════════════════════════════════════════════════════╗
# ║                    EazySSH Installer                        ║
# ║         SSH Terminal as a Telegram Mini App                 ║
# ║                                                             ║
# ║  github.com/Schmi7zz/eazy-ssh                              ║
# ║  t.me/SchmitzWS                                             ║
# ╚══════════════════════════════════════════════════════════════╝
set -e

# ─── Colors & Symbols ───
R='\033[0;31m'    G='\033[0;32m'   Y='\033[1;33m'
B='\033[0;34m'    C='\033[0;36m'   W='\033[1;37m'
D='\033[0;90m'    N='\033[0m'      BG='\033[44m'

TICK="${G}✓${N}"
CROSS="${R}✗${N}"
ARROW="${C}➜${N}"
DOT="${D}·${N}"
WARN="${Y}⚠${N}"

# ─── Helpers ───
banner() {
    clear
    echo -e "${D}"
    echo '  ╔══════════════════════════════════════════════╗'
    echo '  ║                                              ║'
    echo -e "  ║   ${C}⬡${D}  ${W}E a z y S S H${D}   ${D}Installer${D}             ║"
    echo '  ║                                              ║'
    echo '  ║   SSH Terminal as a Telegram Mini App        ║'
    echo '  ║                                              ║'
    echo '  ╚══════════════════════════════════════════════╝'
    echo -e "${N}"
}

step() {
    echo ""
    echo -e "  ${BG}${W} STEP $1 ${N}  ${W}$2${N}"
    echo -e "  ${D}$(printf '%.0s─' {1..46})${N}"
}

info()    { echo -e "  ${DOT} ${D}$1${N}"; }
success() { echo -e "  ${TICK} ${G}$1${N}"; }
warn()    { echo -e "  ${WARN} ${Y}$1${N}"; }
fail()    { echo -e "  ${CROSS} ${R}$1${N}"; }
ask()     { echo -en "  ${ARROW} ${W}$1${N} "; }

confirm() {
    echo ""
    ask "$1 [Y/n]:"
    read -r ans
    [[ -z "$ans" || "$ans" =~ ^[Yy] ]]
}

spinner() {
    local pid=$1
    local spin='⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏'
    local i=0
    while kill -0 "$pid" 2>/dev/null; do
        printf "\r  ${C}${spin:i++%10:1}${N} ${D}$2${N}"
        sleep 0.1
    done
    printf "\r"
}

# ─── Check root ───
if [[ $EUID -ne 0 ]]; then
    echo -e "\n  ${CROSS} ${R}Run as root:${N} sudo bash install.sh\n"
    exit 1
fi

# ═══════════════════════════════════════
#  START
# ═══════════════════════════════════════
banner

echo -e "  ${D}This script will install EazySSH on your server.${N}"
echo -e "  ${D}You'll need: a domain, a Telegram bot token, and 5 minutes.${N}"
echo ""

if ! confirm "Ready to start?"; then
    echo -e "\n  ${D}Bye!${N}\n"
    exit 0
fi

# ═══════════════════════════════════════
#  STEP 1: Collect info
# ═══════════════════════════════════════
banner
step "1/7" "Configuration"
echo ""

info "The domain where your Mini App will be hosted."
info "You need TWO subdomains pointing to this server's IP."
info "Example: ssh-terminal.example.com & ssh-api.example.com"
echo ""

ask "Frontend subdomain (e.g. ssh-terminal.example.com):"
read -r FRONTEND_DOMAIN
while [[ -z "$FRONTEND_DOMAIN" ]]; do
    fail "Cannot be empty"
    ask "Frontend subdomain:"
    read -r FRONTEND_DOMAIN
done

ask "Backend subdomain (e.g. ssh-api.example.com):"
read -r BACKEND_DOMAIN
while [[ -z "$BACKEND_DOMAIN" ]]; do
    fail "Cannot be empty"
    ask "Backend subdomain:"
    read -r BACKEND_DOMAIN
done

echo ""

info "Get this from @BotFather in Telegram (/newbot)"
ask "Bot token:"
read -r BOT_TOKEN
while [[ -z "$BOT_TOKEN" ]]; do
    fail "Cannot be empty"
    ask "Bot token:"
    read -r BOT_TOKEN
done

echo ""

info "Your numeric Telegram user ID (get from @userinfobot)"
ask "Admin Telegram ID:"
read -r ADMIN_ID
while [[ -z "$ADMIN_ID" || ! "$ADMIN_ID" =~ ^[0-9]+$ ]]; do
    fail "Must be a number"
    ask "Admin Telegram ID:"
    read -r ADMIN_ID
done

echo ""

info "Your bot's username (for the Mini App link)"
ask "Bot username (without @, e.g. EazySSH_bot):"
read -r BOT_USERNAME
while [[ -z "$BOT_USERNAME" ]]; do
    fail "Cannot be empty"
    ask "Bot username:"
    read -r BOT_USERNAME
done

ask "Mini App short name (e.g. terminal):"
read -r APP_SHORT
APP_SHORT=${APP_SHORT:-terminal}

echo ""

info "For Let's Encrypt SSL certificate"
ask "Email address:"
read -r SSL_EMAIL
while [[ -z "$SSL_EMAIL" ]]; do
    fail "Cannot be empty"
    ask "Email address:"
    read -r SSL_EMAIL
done

# ─── Summary ───
banner
step "1/7" "Confirm Configuration"
echo ""
echo -e "  ${W}Frontend:${N}     https://${FRONTEND_DOMAIN}"
echo -e "  ${W}Backend:${N}      https://${BACKEND_DOMAIN}"
echo -e "  ${W}Bot:${N}          @${BOT_USERNAME}"
echo -e "  ${W}Mini App:${N}     t.me/${BOT_USERNAME}/${APP_SHORT}"
echo -e "  ${W}Admin ID:${N}     ${ADMIN_ID}"
echo -e "  ${W}SSL Email:${N}    ${SSL_EMAIL}"
echo ""

if ! confirm "Everything correct?"; then
    echo -e "\n  ${D}Run the script again to reconfigure.${N}\n"
    exit 0
fi

# ═══════════════════════════════════════
#  STEP 2: Install dependencies
# ═══════════════════════════════════════
banner
step "2/7" "Installing dependencies"
echo ""

info "Updating packages..."
apt-get update -qq > /dev/null 2>&1 &
spinner $! "Updating package lists..."
success "Package lists updated"

info "Installing nginx, certbot, python3-pip, git..."
apt-get install -y -qq nginx certbot python3-certbot-nginx python3-pip git > /dev/null 2>&1 &
spinner $! "Installing packages..."
success "Packages installed"

if ! command -v docker &> /dev/null; then
    warn "Docker not found. Installing..."
    curl -fsSL https://get.docker.com | sh > /dev/null 2>&1 &
    spinner $! "Installing Docker..."
    success "Docker installed"
else
    success "Docker found: $(docker --version | cut -d' ' -f3 | tr -d ',')"
fi

if docker compose version &> /dev/null; then
    COMPOSE="docker compose"
elif command -v docker-compose &> /dev/null; then
    COMPOSE="docker-compose"
else
    info "Installing docker-compose..."
    apt-get install -y -qq docker-compose > /dev/null 2>&1 &
    spinner $! "Installing docker-compose..."
    COMPOSE="docker-compose"
fi
success "Compose: $COMPOSE"

info "Installing python-telegram-bot..."
pip3 install python-telegram-bot --break-system-packages -q > /dev/null 2>&1 &
spinner $! "Installing python-telegram-bot..."
success "python-telegram-bot installed"

# ═══════════════════════════════════════
#  STEP 3: Setup project
# ═══════════════════════════════════════
banner
step "3/7" "Setting up project"
echo ""

INSTALL_DIR="/opt/ssh-terminal"

if [[ -d "$INSTALL_DIR" ]]; then
    warn "Directory $INSTALL_DIR already exists"
    if confirm "Remove and reinstall?"; then
        rm -rf "$INSTALL_DIR"
    else
        fail "Aborted"
        exit 1
    fi
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -f "$SCRIPT_DIR/backend/main.go" ]]; then
    cp -r "$SCRIPT_DIR" "$INSTALL_DIR"
    success "Copied from local directory"
else
    git clone https://github.com/Schmi7zz/eazy-ssh.git "$INSTALL_DIR" > /dev/null 2>&1 &
    spinner $! "Cloning repository..."
    success "Repository cloned"
fi

cat > "$INSTALL_DIR/.env" << ENVEOF
BOT_TOKEN=${BOT_TOKEN}
WEBAPP_URL=https://${FRONTEND_DOMAIN}
ADMIN_ID=${ADMIN_ID}
USERS_FILE=${INSTALL_DIR}/users.json
ENVEOF
chmod 600 "$INSTALL_DIR/.env"
success "Environment file created"

FRONTEND_FILE="$INSTALL_DIR/frontend/index.html"
if [[ -f "$FRONTEND_FILE" ]]; then
    sed -i "s|wss://ssh-api.yourdomain.com/ws|wss://${BACKEND_DOMAIN}/ws|g" "$FRONTEND_FILE"
    sed -i "s|https://t.me/YOUR_BOT/YOUR_APP|https://t.me/${BOT_USERNAME}/${APP_SHORT}|g" "$FRONTEND_FILE"
    success "Frontend configured"
else
    fail "frontend/index.html not found!"
    exit 1
fi

# ═══════════════════════════════════════
#  STEP 4: Build backend
# ═══════════════════════════════════════
banner
step "4/7" "Building backend"
echo ""

cd "$INSTALL_DIR"
info "Building Docker container (this may take a minute)..."
$COMPOSE up -d --build > /dev/null 2>&1 &
spinner $! "Building Go backend..."

sleep 2
if curl -s http://localhost:8080/health | grep -q "ok"; then
    success "Backend is running"
else
    warn "Backend might still be starting... checking again in 5s"
    sleep 5
    if curl -s http://localhost:8080/health | grep -q "ok"; then
        success "Backend is running"
    else
        fail "Backend failed to start. Check: $COMPOSE logs"
        exit 1
    fi
fi

# ═══════════════════════════════════════
#  STEP 5: Configure Nginx
# ═══════════════════════════════════════
banner
step "5/7" "Configuring Nginx"
echo ""

NGINX_CONF="/etc/nginx/sites-available/ssh-terminal"

cat > "$NGINX_CONF" << NGINXEOF
server {
    listen 80;
    server_name ${FRONTEND_DOMAIN};

    root ${INSTALL_DIR}/frontend;
    index index.html;

    location / {
        try_files \$uri \$uri/ /index.html;
    }
}

server {
    listen 80;
    server_name ${BACKEND_DOMAIN};

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_read_timeout 86400s;
        proxy_send_timeout 86400s;
    }
}
NGINXEOF
success "Nginx config created"

ln -sf "$NGINX_CONF" /etc/nginx/sites-enabled/ssh-terminal

if nginx -t 2>/dev/null; then
    systemctl reload nginx
    success "Nginx reloaded"
else
    fail "Nginx config test failed!"
    nginx -t
    exit 1
fi

# ═══════════════════════════════════════
#  STEP 6: SSL
# ═══════════════════════════════════════
banner
step "6/7" "Getting SSL certificate"
echo ""

info "Requesting certificate from Let's Encrypt..."
info "Make sure DNS records are pointing to this server!"
echo ""

certbot --nginx \
    -d "$FRONTEND_DOMAIN" \
    -d "$BACKEND_DOMAIN" \
    --non-interactive \
    --agree-tos \
    --email "$SSL_EMAIL" \
    --redirect > /dev/null 2>&1 &
spinner $! "Getting SSL certificate..."

if [[ $? -eq 0 ]]; then
    success "SSL certificate installed"
else
    warn "SSL might have failed. You can retry manually:"
    info "certbot --nginx -d $FRONTEND_DOMAIN -d $BACKEND_DOMAIN"
fi

# ═══════════════════════════════════════
#  STEP 7: Start bot
# ═══════════════════════════════════════
banner
step "7/7" "Starting Telegram bot"
echo ""

cat > /etc/systemd/system/ssh-terminal-bot.service << SVCEOF
[Unit]
Description=EazySSH Telegram Bot
After=network.target

[Service]
EnvironmentFile=${INSTALL_DIR}/.env
ExecStart=/usr/bin/python3 ${INSTALL_DIR}/bot.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
SVCEOF

systemctl daemon-reload
systemctl enable ssh-terminal-bot > /dev/null 2>&1
systemctl start ssh-terminal-bot
sleep 2

if systemctl is-active --quiet ssh-terminal-bot; then
    success "Telegram bot is running"
else
    warn "Bot might have issues. Check: journalctl -u ssh-terminal-bot -n 20"
fi

# ═══════════════════════════════════════
#  DONE
# ═══════════════════════════════════════
banner
echo ""
echo -e "  ${G}╔══════════════════════════════════════════════╗${N}"
echo -e "  ${G}║                                              ║${N}"
echo -e "  ${G}║   ${W}✓  Installation Complete!${G}                 ║${N}"
echo -e "  ${G}║                                              ║${N}"
echo -e "  ${G}╚══════════════════════════════════════════════╝${N}"
echo ""
echo -e "  ${W}Your EazySSH is ready!${N}"
echo ""
echo -e "  ${D}┌─────────────────────────────────────────────────┐${N}"
echo -e "  ${D}│${N}  ${C}Frontend${N}    https://${FRONTEND_DOMAIN}"
echo -e "  ${D}│${N}  ${C}Backend${N}     https://${BACKEND_DOMAIN}"
echo -e "  ${D}│${N}  ${C}Mini App${N}    t.me/${BOT_USERNAME}/${APP_SHORT}"
echo -e "  ${D}└─────────────────────────────────────────────────┘${N}"
echo ""
echo -e "  ${W}Next steps — go to @BotFather:${N}"
echo ""
echo -e "     ${D}1.${N} /setmenubutton → select @${BOT_USERNAME}"
echo -e "        URL: ${C}https://${FRONTEND_DOMAIN}${N}"
echo -e "        Title: ${C}Open Terminal${N}"
echo ""
echo -e "     ${D}2.${N} /newapp → select @${BOT_USERNAME}"
echo -e "        Web App URL: ${C}https://${FRONTEND_DOMAIN}${N}"
echo -e "        Short name: ${C}${APP_SHORT}${N}"
echo ""
echo -e "  ${ARROW} Then open ${W}t.me/${BOT_USERNAME}/${APP_SHORT}${N} in Telegram"
echo ""
echo -e "  ${D}──────────────────────────────────────────────────${N}"
echo -e "  ${D}Manage:${N}"
echo -e "  ${D}  Logs:${N}     $COMPOSE -f $INSTALL_DIR/docker-compose.yml logs -f"
echo -e "  ${D}  Restart:${N}  $COMPOSE -f $INSTALL_DIR/docker-compose.yml restart"
echo -e "  ${D}  Bot logs:${N} journalctl -u ssh-terminal-bot -f"
echo -e "  ${D}  Config:${N}   $INSTALL_DIR/.env"
echo -e "  ${D}──────────────────────────────────────────────────${N}"
echo -e "  ${D}Channel:${N} ${C}t.me/SchmitzWS${N}"
echo ""
