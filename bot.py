import json
import os
import asyncio
import html
import traceback
import uuid
from datetime import datetime

import asyncssh
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)

# ─── Config ───
TOKEN = os.getenv("BOT_TOKEN", "")
WEBAPP_URL = os.getenv("WEBAPP_URL", "https://ssh-terminal.yourdomain.com")
USERS_FILE = "/opt/ssh-terminal/users.json"
SERVERS_FILE = "/opt/ssh-terminal/servers_data.json"
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

# Terminal display config
MAX_LINES = 35          # max lines shown in terminal message
OUTPUT_BUFFER_SEC = 1.5 # seconds between message edits
MAX_MSG_LEN = 4000      # telegram limit safety margin

# ─── Conversation states ───
(
    ADD_LABEL,
    ADD_HOST,
    ADD_PORT,
    ADD_USERNAME,
    ADD_AUTH_TYPE,
    ADD_PASSWORD,
    ADD_KEY,
    ADD_PASSPHRASE,
    EDIT_CHOOSE_FIELD,
    EDIT_VALUE,
) = range(10)

# ─── Storage helpers ───

def load_users():
    try:
        with open(USERS_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {}


def save_users(users):
    with open(USERS_FILE, "w") as f:
        json.dump(users, f, ensure_ascii=False, indent=2)


def load_servers_data():
    try:
        with open(SERVERS_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {}


def save_servers_data(data):
    with open(SERVERS_FILE, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_user_servers(user_id: int) -> list:
    data = load_servers_data()
    return data.get(str(user_id), {}).get("servers", [])


def save_user_servers(user_id: int, servers: list):
    data = load_servers_data()
    if str(user_id) not in data:
        data[str(user_id)] = {}
    data[str(user_id)]["servers"] = servers
    save_servers_data(data)


def find_server(user_id: int, server_id: str):
    servers = get_user_servers(user_id)
    for s in servers:
        if s["id"] == server_id:
            return s
    return None


# ─── Active SSH Sessions ───
# user_id -> session info
active_sessions = {}


class SSHSession:
    """Manages a single SSH connection for chat terminal."""

    def __init__(self, user_id: int, server: dict, bot, chat_id: int, message_id: int):
        self.user_id = user_id
        self.server = server
        self.bot = bot
        self.chat_id = chat_id
        self.message_id = message_id  # the terminal display message
        self.conn = None
        self.process = None
        self.output_lines = []
        self.output_buffer = ""
        self.buffer_lock = asyncio.Lock()
        self.update_task = None
        self.alive = False
        self.last_edit = 0

    async def connect(self):
        """Establish SSH connection."""
        srv = self.server
        port = int(srv.get("port", 22))

        connect_kwargs = {
            "host": srv["host"],
            "port": port,
            "username": srv["username"],
            "known_hosts": None,
        }

        if srv.get("auth_type") == "key":
            key_data = srv.get("private_key", "")
            passphrase = srv.get("passphrase") or None
            try:
                pkey = asyncssh.import_private_key(key_data, passphrase)
                connect_kwargs["client_keys"] = [pkey]
            except Exception as e:
                raise Exception(f"SSH Key error: {e}")
        else:
            connect_kwargs["password"] = srv.get("password", "")

        self.conn = await asyncio.wait_for(
            asyncssh.connect(**connect_kwargs),
            timeout=15,
        )

        self.process = await self.conn.create_process(
            term_type="xterm",
            term_size=(80, 24),
        )

        self.alive = True
        self.update_task = asyncio.create_task(self._output_loop())
        asyncio.create_task(self._read_loop())

    async def _read_loop(self):
        """Read stdout from SSH and buffer it."""
        try:
            while self.alive:
                data = await self.process.stdout.read(4096)
                if not data:
                    break
                async with self.buffer_lock:
                    self.output_buffer += data
        except Exception:
            pass
        finally:
            self.alive = False
            # Final update
            await self._flush_and_update()
            await self._update_terminal_message(disconnected=True)

    async def _output_loop(self):
        """Periodically flush buffer and edit the terminal message."""
        while self.alive:
            await asyncio.sleep(OUTPUT_BUFFER_SEC)
            await self._flush_and_update()

    async def _flush_and_update(self):
        """Process buffer into lines and update message."""
        async with self.buffer_lock:
            if not self.output_buffer:
                return
            raw = self.output_buffer
            self.output_buffer = ""

        # Strip ANSI escape codes for cleaner display
        clean = self._strip_ansi(raw)

        # Process into lines
        for char in clean:
            if char == "\r":
                continue
            elif char == "\n":
                self.output_lines.append("")
            elif char == "\b":
                if self.output_lines and self.output_lines[-1]:
                    self.output_lines[-1] = self.output_lines[-1][:-1]
            else:
                if not self.output_lines:
                    self.output_lines.append("")
                self.output_lines[-1] += char

        # Trim to max lines
        if len(self.output_lines) > MAX_LINES * 2:
            self.output_lines = self.output_lines[-MAX_LINES:]

        await self._update_terminal_message()

    def _strip_ansi(self, text):
        """Remove ANSI escape sequences."""
        import re
        return re.sub(r'\x1b\[[0-9;]*[a-zA-Z]|\x1b\][^\x07]*\x07|\x1b[()][0-9A-B]|\x1b\[[\?]?[0-9;]*[a-zA-Z]', '', text)

    async def _update_terminal_message(self, disconnected=False):
        """Edit the terminal message with current output."""
        import time
        now = time.time()

        # Rate limit: don't edit more than once per second
        if not disconnected and (now - self.last_edit) < 1.0:
            return

        lines = self.output_lines[-MAX_LINES:] if self.output_lines else [""]
        terminal_text = "\n".join(lines)

        # Truncate if too long
        if len(terminal_text) > MAX_MSG_LEN - 200:
            terminal_text = terminal_text[-(MAX_MSG_LEN - 200):]

        # Escape HTML
        terminal_text = html.escape(terminal_text)

        srv = self.server
        label = srv.get("label") or srv["host"]

        if disconnected:
            status_line = "🔴 Disconnected"
        else:
            status_line = "🟢 Connected"

        msg = (
            f"🖥 <b>Terminal — {html.escape(label)}</b>\n"
            f"<code>{srv['username']}@{srv['host']}:{srv.get('port', 22)}</code>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"<pre>{terminal_text}</pre>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"{status_line}"
        )

        # Build keyboard
        if disconnected:
            keyboard = [[
                InlineKeyboardButton("🔄 Reconnect", callback_data=f"reconnect:{srv['id']}"),
                InlineKeyboardButton("🏠 Home", callback_data="menu:main"),
            ]]
        else:
            keyboard = [
                [
                    InlineKeyboardButton("⏎ Enter", callback_data="term:enter"),
                    InlineKeyboardButton("⛔ Ctrl+C", callback_data="term:ctrlc"),
                ],
                [
                    InlineKeyboardButton("✂️ Ctrl+X", callback_data="term:ctrlx"),
                    InlineKeyboardButton("📎 Ctrl+B", callback_data="term:ctrlb"),
                ],
                [
                    InlineKeyboardButton("⏹ Disconnect", callback_data="term:disconnect"),
                    InlineKeyboardButton("🧹 Clear", callback_data="term:clear"),
                ],
            ]

        try:
            await self.bot.edit_message_text(
                text=msg,
                chat_id=self.chat_id,
                message_id=self.message_id,
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
            self.last_edit = now
        except Exception:
            pass  # message not modified or rate limited

    async def send_input(self, text):
        """Send user input to SSH."""
        if self.process and self.alive:
            self.process.stdin.write(text + "\n")

    async def send_raw(self, data):
        """Send raw data to SSH (no newline appended)."""
        if self.process and self.alive:
            self.process.stdin.write(data)

    async def disconnect(self):
        """Close the SSH session."""
        self.alive = False
        if self.update_task:
            self.update_task.cancel()
        try:
            if self.process:
                self.process.close()
        except Exception:
            pass
        try:
            if self.conn:
                self.conn.close()
        except Exception:
            pass


# ─── Keyboard builders ───

def main_menu_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🖥 Web Terminal", web_app=WebAppInfo(url=WEBAPP_URL))],
        [InlineKeyboardButton("📟 Chat Terminal", callback_data="menu:chat_terminal")],
    ])


def chat_terminal_keyboard(user_id: int):
    servers = get_user_servers(user_id)
    rows = []
    for srv in servers:
        label = srv.get("label") or srv["host"]
        emoji = "🟢" if user_id in active_sessions else "⚪"
        rows.append([InlineKeyboardButton(
            f"{emoji} {label} — {srv['username']}@{srv['host']}",
            callback_data=f"srv:select:{srv['id']}",
        )])
    rows.append([InlineKeyboardButton("➕ Add Server", callback_data="srv:add")])
    rows.append([InlineKeyboardButton("🔙 Back", callback_data="menu:main")])
    return InlineKeyboardMarkup(rows)


def server_action_keyboard(server_id: str):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("⚡ Connect", callback_data=f"srv:connect:{server_id}"),
            InlineKeyboardButton("📂 SFTP", callback_data=f"srv:sftp:{server_id}"),
        ],
        [
            InlineKeyboardButton("✏️ Edit", callback_data=f"srv:edit:{server_id}"),
            InlineKeyboardButton("🗑 Delete", callback_data=f"srv:delete:{server_id}"),
        ],
        [InlineKeyboardButton("🔙 Back", callback_data="menu:chat_terminal")],
    ])


def confirm_delete_keyboard(server_id: str):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Yes, delete", callback_data=f"srv:confirm_delete:{server_id}"),
            InlineKeyboardButton("❌ Cancel", callback_data=f"srv:select:{server_id}"),
        ],
    ])


def auth_type_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔒 Password", callback_data="auth:password"),
            InlineKeyboardButton("🔑 SSH Key", callback_data="auth:key"),
        ],
    ])


def cancel_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ Cancel", callback_data="srv:cancel_add")],
    ])


def edit_field_keyboard(server_id: str):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📛 Label", callback_data=f"edit:label:{server_id}"),
            InlineKeyboardButton("🌐 Host", callback_data=f"edit:host:{server_id}"),
        ],
        [
            InlineKeyboardButton("🔌 Port", callback_data=f"edit:port:{server_id}"),
            InlineKeyboardButton("👤 Username", callback_data=f"edit:username:{server_id}"),
        ],
        [
            InlineKeyboardButton("🔒 Password", callback_data=f"edit:password:{server_id}"),
            InlineKeyboardButton("🔑 Key", callback_data=f"edit:private_key:{server_id}"),
        ],
        [InlineKeyboardButton("🔙 Back", callback_data=f"srv:select:{server_id}")],
    ])


# ─── Command Handlers ───

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    users = load_users()
    users[str(user.id)] = {
        "name": user.full_name,
        "username": user.username or "",
    }
    save_users(users)

    await update.message.reply_text(
        f"👋 <b>Welcome to EazySSH!</b>\n\n"
        f"Connect to your servers securely from Telegram.\n"
        f"Choose your preferred terminal:",
        parse_mode="HTML",
        reply_markup=main_menu_keyboard(),
    )


async def disconnect_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid in active_sessions:
        session = active_sessions[uid]
        await session.disconnect()
        del active_sessions[uid]
        await update.message.reply_text(
            "🔴 Session disconnected.",
            reply_markup=main_menu_keyboard(),
        )
    else:
        await update.message.reply_text("❌ No active session.")


# ─── Callback Query Handler ───

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    uid = query.from_user.id

    # ─── Menu navigation ───
    if data == "menu:main":
        await query.edit_message_text(
            f"👋 <b>Welcome to EazySSH!</b>\n\n"
            f"Connect to your servers securely from Telegram.\n"
            f"Choose your preferred terminal:",
            parse_mode="HTML",
            reply_markup=main_menu_keyboard(),
        )
        return

    if data == "menu:chat_terminal":
        servers = get_user_servers(uid)
        count = len(servers)
        if count > 0:
            text = f"📟 <b>Chat Terminal</b>\n\n📡 You have <b>{count}</b> server(s).\nSelect a server to connect:"
        else:
            text = f"📟 <b>Chat Terminal</b>\n\n📡 No servers yet.\nTap ➕ to add your first server!"
        await query.edit_message_text(
            text, parse_mode="HTML",
            reply_markup=chat_terminal_keyboard(uid),
        )
        return

    # ─── Server selection ───
    if data.startswith("srv:select:"):
        server_id = data.split(":", 2)[2]
        srv = find_server(uid, server_id)
        if not srv:
            await query.edit_message_text("❌ Server not found.",
                reply_markup=chat_terminal_keyboard(uid))
            return
        label = srv.get("label") or srv["host"]
        auth_icon = "🔑" if srv.get("auth_type") == "key" else "🔒"
        text = (
            f"🖥 <b>{html.escape(label)}</b>\n\n"
            f"🌐 Host: <code>{html.escape(srv['host'])}</code>\n"
            f"🔌 Port: <code>{srv.get('port', 22)}</code>\n"
            f"👤 User: <code>{html.escape(srv['username'])}</code>\n"
            f"{auth_icon} Auth: {srv.get('auth_type', 'password')}\n"
        )
        await query.edit_message_text(
            text, parse_mode="HTML",
            reply_markup=server_action_keyboard(server_id),
        )
        return

    # ─── Connect to server ───
    if data.startswith("srv:connect:"):
        server_id = data.split(":", 2)[2]
        srv = find_server(uid, server_id)
        if not srv:
            await query.edit_message_text("❌ Server not found.")
            return

        # Close existing session if any
        if uid in active_sessions:
            await active_sessions[uid].disconnect()
            del active_sessions[uid]

        label = srv.get("label") or srv["host"]
        await query.edit_message_text(
            f"🖥 <b>Terminal — {html.escape(label)}</b>\n"
            f"<code>{srv['username']}@{srv['host']}:{srv.get('port', 22)}</code>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"<pre>⏳ Connecting...</pre>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🟡 Connecting",
            parse_mode="HTML",
        )

        session = SSHSession(
            user_id=uid,
            server=srv,
            bot=context.bot,
            chat_id=query.message.chat_id,
            message_id=query.message.message_id,
        )

        try:
            await session.connect()
            active_sessions[uid] = session
        except asyncio.TimeoutError:
            await query.edit_message_text(
                f"🖥 <b>Terminal — {html.escape(label)}</b>\n\n"
                f"❌ <b>Connection timed out</b>\n"
                f"Server did not respond within 15 seconds.",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔄 Retry", callback_data=f"srv:connect:{server_id}")],
                    [InlineKeyboardButton("🔙 Back", callback_data="menu:chat_terminal")],
                ]),
            )
        except Exception as e:
            err_msg = str(e)
            if len(err_msg) > 200:
                err_msg = err_msg[:200] + "..."
            await query.edit_message_text(
                f"🖥 <b>Terminal — {html.escape(label)}</b>\n\n"
                f"❌ <b>Connection failed</b>\n"
                f"<code>{html.escape(err_msg)}</code>",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔄 Retry", callback_data=f"srv:connect:{server_id}")],
                    [InlineKeyboardButton("🔙 Back", callback_data="menu:chat_terminal")],
                ]),
            )
        return

    # ─── Reconnect ───
    if data.startswith("reconnect:"):
        server_id = data.split(":", 1)[1]
        # Reuse connect logic
        query.data = f"srv:connect:{server_id}"
        await callback_handler(update, context)
        return

    # ─── Terminal actions ───
    if data == "term:disconnect":
        if uid in active_sessions:
            session = active_sessions[uid]
            await session.disconnect()
            del active_sessions[uid]
        await query.edit_message_text(
            "🔴 Session disconnected.\n\nWhat would you like to do?",
            reply_markup=main_menu_keyboard(),
        )
        return

    if data == "term:clear":
        if uid in active_sessions:
            active_sessions[uid].output_lines = []
            await active_sessions[uid]._update_terminal_message()
        return

    if data == "term:enter":
        if uid in active_sessions:
            await active_sessions[uid].send_raw("\n")
        return

    if data == "term:ctrlc":
        if uid in active_sessions:
            await active_sessions[uid].send_raw("\x03")
        return

    if data == "term:ctrlx":
        if uid in active_sessions:
            await active_sessions[uid].send_raw("\x18")
        return

    if data == "term:ctrlb":
        if uid in active_sessions:
            await active_sessions[uid].send_raw("\x02")
        return

    # ─── Delete server ───
    if data.startswith("srv:delete:"):
        server_id = data.split(":", 2)[2]
        srv = find_server(uid, server_id)
        if not srv:
            return
        label = srv.get("label") or srv["host"]
        await query.edit_message_text(
            f"🗑 <b>Delete server?</b>\n\n"
            f"Are you sure you want to delete <b>{html.escape(label)}</b>?\n"
            f"This cannot be undone.",
            parse_mode="HTML",
            reply_markup=confirm_delete_keyboard(server_id),
        )
        return

    if data.startswith("srv:confirm_delete:"):
        server_id = data.split(":", 2)[2]
        servers = get_user_servers(uid)
        servers = [s for s in servers if s["id"] != server_id]
        save_user_servers(uid, servers)
        await query.edit_message_text(
            "✅ Server deleted.",
            reply_markup=chat_terminal_keyboard(uid),
        )
        return

    # ─── Add Server (start flow) ───
    if data == "srv:add":
        context.user_data["adding_server"] = {"step": "label"}
        await query.edit_message_text(
            "➕ <b>Add New Server</b>\n\n"
            "📛 Enter a <b>label</b> for this server\n"
            "<i>(or send  -  to skip)</i>",
            parse_mode="HTML",
            reply_markup=cancel_keyboard(),
        )
        return

    if data == "srv:cancel_add":
        context.user_data.pop("adding_server", None)
        context.user_data.pop("editing_server", None)
        await query.edit_message_text(
            "❌ Cancelled.",
            reply_markup=chat_terminal_keyboard(uid),
        )
        return

    # ─── Auth type selection (during add) ───
    if data.startswith("auth:"):
        auth_type = data.split(":")[1]
        adding = context.user_data.get("adding_server")
        if not adding:
            return

        adding["data"]["auth_type"] = auth_type

        if auth_type == "password":
            adding["step"] = "password"
            await query.edit_message_text(
                "🔒 Enter the <b>password</b>:",
                parse_mode="HTML",
                reply_markup=cancel_keyboard(),
            )
        else:
            adding["step"] = "private_key"
            await query.edit_message_text(
                "🔑 Paste your <b>private SSH key</b>:",
                parse_mode="HTML",
                reply_markup=cancel_keyboard(),
            )
        return

    # ─── Edit server ───
    if data.startswith("srv:edit:"):
        server_id = data.split(":", 2)[2]
        srv = find_server(uid, server_id)
        if not srv:
            return
        label = srv.get("label") or srv["host"]
        await query.edit_message_text(
            f"✏️ <b>Edit: {html.escape(label)}</b>\n\n"
            f"Select field to edit:",
            parse_mode="HTML",
            reply_markup=edit_field_keyboard(server_id),
        )
        return

    if data.startswith("edit:"):
        parts = data.split(":", 2)
        field = parts[1]
        server_id = parts[2]

        field_names = {
            "label": "📛 Label",
            "host": "🌐 Host",
            "port": "🔌 Port",
            "username": "👤 Username",
            "password": "🔒 Password",
            "private_key": "🔑 Private Key",
        }

        context.user_data["editing_server"] = {
            "server_id": server_id,
            "field": field,
        }

        await query.edit_message_text(
            f"✏️ Enter new value for <b>{field_names.get(field, field)}</b>:",
            parse_mode="HTML",
            reply_markup=cancel_keyboard(),
        )
        return

    # ─── SFTP (redirect to web app) ───
    if data.startswith("srv:sftp:"):
        await query.edit_message_text(
            "📂 SFTP is available in the Web Terminal.\n"
            "Open the Mini App to use SFTP:",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🖥 Open Web Terminal", web_app=WebAppInfo(url=WEBAPP_URL))],
                [InlineKeyboardButton("🔙 Back", callback_data="menu:chat_terminal")],
            ]),
        )
        return


# ─── Message Handler (text input) ───

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    text = update.message.text

    # ─── Check if user is adding a server ───
    adding = context.user_data.get("adding_server")
    if adding:
        await _handle_add_server_step(update, context, adding, text)
        return

    # ─── Check if user is editing a server ───
    editing = context.user_data.get("editing_server")
    if editing:
        await _handle_edit_server(update, context, editing, text)
        return

    # ─── Check if user has active terminal session ───
    if uid in active_sessions:
        session = active_sessions[uid]
        if session.alive:
            await session.send_input(text)
            # Delete user's message to keep chat clean
            try:
                await update.message.delete()
            except Exception:
                pass
            return

    # ─── Broadcast handler (admin only) ───
    if uid == ADMIN_ID and ADMIN_ID in broadcast_pending:
        await handle_broadcast_message(update, context)
        return


async def _handle_add_server_step(update, context, adding, text):
    """Handle multi-step server addition."""
    uid = update.effective_user.id
    step = adding.get("step", "label")

    if step == "label":
        adding["data"] = {"label": text if text != "-" else ""}
        adding["step"] = "host"
        await update.message.reply_text(
            "🌐 Enter the server <b>host</b> (IP or domain):",
            parse_mode="HTML",
            reply_markup=cancel_keyboard(),
        )
        return

    if step == "host":
        adding["data"]["host"] = text.strip()
        adding["step"] = "port"
        await update.message.reply_text(
            "🔌 Enter the <b>port</b>\n<i>(or send  -  for default 22)</i>:",
            parse_mode="HTML",
            reply_markup=cancel_keyboard(),
        )
        return

    if step == "port":
        port = 22
        if text.strip() != "-":
            try:
                port = int(text.strip())
            except ValueError:
                await update.message.reply_text("⚠️ Invalid port. Enter a number:")
                return
        adding["data"]["port"] = port
        adding["step"] = "username"
        await update.message.reply_text(
            "👤 Enter the <b>username</b>:",
            parse_mode="HTML",
            reply_markup=cancel_keyboard(),
        )
        return

    if step == "username":
        adding["data"]["username"] = text.strip()
        adding["step"] = "auth_type"
        await update.message.reply_text(
            "🔐 Choose authentication method:",
            reply_markup=auth_type_keyboard(),
        )
        return

    if step == "password":
        adding["data"]["password"] = text
        adding["data"]["private_key"] = ""
        adding["data"]["passphrase"] = ""
        # Delete the password message for security
        try:
            await update.message.delete()
        except Exception:
            pass
        await _finish_add_server(update, context, adding)
        return

    if step == "private_key":
        adding["data"]["private_key"] = text
        adding["data"]["password"] = ""
        adding["step"] = "passphrase"
        # Delete the key message for security
        try:
            await update.message.delete()
        except Exception:
            pass
        await update.message.reply_text(
            "🔑 Enter the <b>passphrase</b>\n<i>(or send  -  if none)</i>:",
            parse_mode="HTML",
            reply_markup=cancel_keyboard(),
        )
        return

    if step == "passphrase":
        adding["data"]["passphrase"] = text if text != "-" else ""
        # Delete passphrase message for security
        try:
            await update.message.delete()
        except Exception:
            pass
        await _finish_add_server(update, context, adding)
        return


async def _finish_add_server(update, context, adding):
    """Save the new server and show confirmation."""
    uid = update.effective_user.id
    data = adding["data"]
    data["id"] = str(uuid.uuid4())[:8]

    servers = get_user_servers(uid)
    servers.append(data)
    save_user_servers(uid, servers)

    context.user_data.pop("adding_server", None)

    label = data.get("label") or data["host"]
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=(
            f"✅ <b>Server added!</b>\n\n"
            f"📛 {html.escape(label)}\n"
            f"🌐 {html.escape(data['host'])}:{data.get('port', 22)}\n"
            f"👤 {html.escape(data['username'])}"
        ),
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("⚡ Connect now", callback_data=f"srv:connect:{data['id']}")],
            [InlineKeyboardButton("🔙 Server list", callback_data="menu:chat_terminal")],
        ]),
    )


async def _handle_edit_server(update, context, editing, text):
    """Handle editing a server field."""
    uid = update.effective_user.id
    server_id = editing["server_id"]
    field = editing["field"]

    servers = get_user_servers(uid)
    for srv in servers:
        if srv["id"] == server_id:
            if field == "port":
                try:
                    srv[field] = int(text.strip())
                except ValueError:
                    await update.message.reply_text("⚠️ Invalid port. Enter a number:")
                    return
            else:
                srv[field] = text

    save_user_servers(uid, servers)
    context.user_data.pop("editing_server", None)

    # Delete sensitive fields
    if field in ("password", "private_key", "passphrase"):
        try:
            await update.message.delete()
        except Exception:
            pass

    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=f"✅ <b>{field.replace('_', ' ').title()}</b> updated!",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 Back to server", callback_data=f"srv:select:{server_id}")],
        ]),
    )


# ─── Admin Commands ───

broadcast_pending = {}


async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Access denied.")
        return

    users = load_users()
    text = f"👥 Total users: {len(users)}\n"
    text += f"🖥 Active sessions: {len(active_sessions)}\n\n"
    for uid, info in list(users.items())[-20:]:
        uname = f"@{info['username']}" if info["username"] else "—"
        active = "🟢" if int(uid) in active_sessions else "⚪"
        text += f"{active} {info['name']} ({uname}) [{uid}]\n"

    if len(users) > 20:
        text += f"\n... and {len(users) - 20} more"

    await update.message.reply_text(text)


async def broadcast_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Access denied.")
        return

    users = load_users()
    count = len(users)

    if not context.args:
        broadcast_pending[ADMIN_ID] = True
        await update.message.reply_text(
            f"📢 Broadcast mode ON\n\n"
            f"Send your message now (text, photo, video, document — anything).\n"
            f"It will be sent to all {count} users.\n\n"
            f"Send /cancel to abort."
        )
        return

    text = " ".join(context.args)
    await do_broadcast_text(update, context, text)


async def handle_broadcast_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    if ADMIN_ID not in broadcast_pending:
        return

    del broadcast_pending[ADMIN_ID]

    users = load_users()
    count = len(users)
    sent = 0
    failed = 0

    status_msg = await update.message.reply_text(f"📤 Sending to {count} users...")

    for uid in users.keys():
        try:
            if update.message.photo:
                await context.bot.send_photo(
                    chat_id=int(uid),
                    photo=update.message.photo[-1].file_id,
                    caption=update.message.caption or "",
                    parse_mode="HTML",
                )
            elif update.message.video:
                await context.bot.send_video(
                    chat_id=int(uid),
                    video=update.message.video.file_id,
                    caption=update.message.caption or "",
                    parse_mode="HTML",
                )
            elif update.message.document:
                await context.bot.send_document(
                    chat_id=int(uid),
                    document=update.message.document.file_id,
                    caption=update.message.caption or "",
                    parse_mode="HTML",
                )
            elif update.message.animation:
                await context.bot.send_animation(
                    chat_id=int(uid),
                    animation=update.message.animation.file_id,
                    caption=update.message.caption or "",
                    parse_mode="HTML",
                )
            elif update.message.text:
                await context.bot.send_message(
                    chat_id=int(uid),
                    text=update.message.text,
                    parse_mode="HTML",
                )
            sent += 1
        except Exception:
            failed += 1

        await asyncio.sleep(0.05)

    await status_msg.edit_text(f"✅ Broadcast complete!\n\n📤 Sent: {sent}\n❌ Failed: {failed}")


async def do_broadcast_text(update, context, text):
    users = load_users()
    count = len(users)
    sent = 0
    failed = 0

    status_msg = await update.message.reply_text(f"📤 Sending to {count} users...")

    for uid in users.keys():
        try:
            await context.bot.send_message(
                chat_id=int(uid),
                text=text,
                parse_mode="HTML",
            )
            sent += 1
        except Exception:
            failed += 1
        await asyncio.sleep(0.05)

    await status_msg.edit_text(f"✅ Broadcast complete!\n\n📤 Sent: {sent}\n❌ Failed: {failed}")


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if ADMIN_ID in broadcast_pending:
        del broadcast_pending[ADMIN_ID]
        await update.message.reply_text("❌ Broadcast cancelled.")

    # Also cancel any add/edit flow
    context.user_data.pop("adding_server", None)
    context.user_data.pop("editing_server", None)


# ─── Main ───

def main():
    if not TOKEN:
        print("ERROR: BOT_TOKEN environment variable is required")
        return

    async def post_init(application):
        from telegram import MenuButtonWebApp
        try:
            await application.bot.set_chat_menu_button(
                menu_button=MenuButtonWebApp(text="🖥 Terminal", web_app=WebAppInfo(url=WEBAPP_URL))
            )
            print(f"Menu button updated to: {WEBAPP_URL}")
        except Exception as e:
            print(f"Failed to set menu button: {e}")

    app = Application.builder().token(TOKEN).post_init(post_init).build()

    # Commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(CommandHandler("broadcast", broadcast_cmd))
    app.add_handler(CommandHandler("cancel", cancel))
    app.add_handler(CommandHandler("disconnect", disconnect_cmd))

    # Callback queries (inline buttons)
    app.add_handler(CallbackQueryHandler(callback_handler))

    # Text messages
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    # Media messages (for broadcast)
    app.add_handler(MessageHandler(
        (filters.PHOTO | filters.VIDEO | filters.Document.ALL | filters.ANIMATION)
        & ~filters.COMMAND,
        handle_text,
    ))

    print("Bot running...")
    app.run_polling()


if __name__ == "__main__":
    main()
