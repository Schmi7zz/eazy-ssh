import json
import os
import re
import time
import asyncio
import html
import traceback
import uuid
import tempfile
import stat as stat_mod
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
WEBAPP_URL = os.getenv("WEBAPP_URL", "")
USERS_FILE = "/opt/ssh-terminal/users.json"
SERVERS_FILE = "/opt/ssh-terminal/servers_data.json"
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

# Required channel membership
REQUIRED_CHANNEL = os.getenv("REQUIRED_CHANNEL", "")
REQUIRED_CHANNEL_URL = os.getenv("REQUIRED_CHANNEL_URL", "")

# Terminal display config
MAX_LINES = 35          # max lines shown in terminal message
OUTPUT_BUFFER_SEC = 1.5 # seconds between message edits
LOG_BUFFER_SEC = 2.0    # seconds between log messages
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

# ─── Active SFTP Sessions ───
# user_id -> sftp state
sftp_sessions = {}

SFTP_PAGE_SIZE = 8  # files per page in chat listing


# ─── Channel Membership Check ───

# Cache to avoid spamming getChatMember API: user_id -> (is_member, timestamp)
_membership_cache = {}
_MEMBERSHIP_CACHE_TTL = 10  # seconds


async def is_channel_member(bot, user_id: int) -> bool:
    """Check if user is member of REQUIRED_CHANNEL. Cached for 10 seconds."""
    # No channel configured → allow everyone
    if not REQUIRED_CHANNEL:
        return True

    # Admin bypass
    if user_id == ADMIN_ID:
        return True

    # Check cache
    cached = _membership_cache.get(user_id)
    if cached:
        is_member, ts = cached
        if time.time() - ts < _MEMBERSHIP_CACHE_TTL:
            return is_member

    try:
        member = await bot.get_chat_member(chat_id=REQUIRED_CHANNEL, user_id=user_id)
        # Valid member statuses
        is_member = member.status in ("creator", "administrator", "member", "restricted")
    except Exception:
        # If channel check fails (bot not admin, user never interacted, etc.)
        is_member = False

    _membership_cache[user_id] = (is_member, time.time())
    return is_member


def invalidate_membership_cache(user_id: int):
    """Clear cached membership status for a user."""
    _membership_cache.pop(user_id, None)


def join_channel_keyboard():
    """Keyboard shown to non-members."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 عضویت در کانال", url=REQUIRED_CHANNEL_URL)],
        [InlineKeyboardButton("🔄 بررسی مجدد", callback_data="check_membership")],
    ])


async def require_membership(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """
    Check membership and show join prompt if not member.
    Returns True if user is member (can proceed), False otherwise.
    """
    uid = update.effective_user.id
    if await is_channel_member(context.bot, uid):
        return True

    # Not a member → close any active sessions
    if uid in active_sessions:
        try:
            await active_sessions[uid].disconnect()
        except Exception:
            pass
        active_sessions.pop(uid, None)
    if uid in sftp_sessions:
        await sftp_close(uid)

    text = (
        f"🔒 <b>دسترسی محدود</b>\n\n"
        f"برای استفاده از ربات باید عضو کانال زیر باشید:\n"
        f"👉 {REQUIRED_CHANNEL}\n\n"
        f"بعد از عضویت روی <b>بررسی مجدد</b> بزنید."
    )

    try:
        if update.callback_query:
            await update.callback_query.answer()
            await update.callback_query.edit_message_text(
                text, parse_mode="HTML", reply_markup=join_channel_keyboard(),
            )
        else:
            await update.message.reply_text(
                text, parse_mode="HTML", reply_markup=join_channel_keyboard(),
            )
    except Exception:
        pass
    return False


class VT100Screen:
    """Lightweight VT100 screen buffer for proper full-screen app rendering."""

    def __init__(self, cols=80, rows=24):
        self.cols = cols
        self.rows = rows
        self.buffer = [self._empty_row() for _ in range(rows)]
        self.cursor_row = 0
        self.cursor_col = 0
        self.saved_cursor = (0, 0)
        # Scrollback for shell output (lines that scrolled off screen)
        self.scrollback = []
        self.max_scrollback = 200

    def _empty_row(self):
        return [" "] * self.cols

    def feed(self, data):
        """Process raw terminal data (with ANSI escape codes)."""
        i = 0
        n = len(data)
        while i < n:
            ch = data[i]

            if ch == "\x1b":  # ESC
                i, consumed = self._parse_escape(data, i)
                if not consumed:
                    i += 1
                continue

            if ch == "\r":
                self.cursor_col = 0
                i += 1
                continue

            if ch == "\n":
                self._line_feed()
                i += 1
                continue

            if ch == "\b":
                self.cursor_col = max(0, self.cursor_col - 1)
                i += 1
                continue

            if ch == "\t":
                # Tab: advance to next multiple of 8
                self.cursor_col = min(self.cols - 1, (self.cursor_col // 8 + 1) * 8)
                i += 1
                continue

            if ch == "\x07":  # BEL
                i += 1
                continue

            # Printable character
            if ord(ch) >= 32:
                if self.cursor_col >= self.cols:
                    # Auto-wrap
                    self.cursor_col = 0
                    self._line_feed()
                self.buffer[self.cursor_row][self.cursor_col] = ch
                self.cursor_col += 1

            i += 1

    def _parse_escape(self, data, pos):
        """Parse escape sequence starting at pos. Returns (new_pos, consumed)."""
        if pos + 1 >= len(data):
            return pos, False

        ch2 = data[pos + 1]

        # CSI: ESC [
        if ch2 == "[":
            return self._parse_csi(data, pos + 2)

        # OSC: ESC ]
        if ch2 == "]":
            # Skip until BEL or ST
            j = pos + 2
            while j < len(data):
                if data[j] == "\x07":
                    return j + 1, True
                if data[j] == "\x1b" and j + 1 < len(data) and data[j + 1] == "\\":
                    return j + 2, True
                j += 1
            return len(data), True

        # Character set: ESC ( or ESC )
        if ch2 in ("(", ")"):
            return pos + 3 if pos + 2 < len(data) else pos + 2, True

        # ESC = or ESC > (keypad modes)
        if ch2 in ("=", ">"):
            return pos + 2, True

        # ESC 7 (save cursor) / ESC 8 (restore cursor)
        if ch2 == "7":
            self.saved_cursor = (self.cursor_row, self.cursor_col)
            return pos + 2, True
        if ch2 == "8":
            self.cursor_row, self.cursor_col = self.saved_cursor
            return pos + 2, True

        return pos + 2, True

    def _parse_csi(self, data, pos):
        """Parse CSI sequence (after ESC [). Returns (new_pos, consumed)."""
        params_str = ""
        private = False
        j = pos

        # Check for private marker
        if j < len(data) and data[j] == "?":
            private = True
            j += 1

        # Collect parameter bytes
        while j < len(data) and (data[j].isdigit() or data[j] == ";"):
            params_str += data[j]
            j += 1

        if j >= len(data):
            return len(data), True

        cmd = data[j]
        j += 1

        # Parse parameters
        params = []
        if params_str:
            for p in params_str.split(";"):
                try:
                    params.append(int(p))
                except ValueError:
                    params.append(0)

        if private:
            # Private CSI sequences (cursor show/hide, etc.) - ignore
            return j, True

        # Execute CSI command
        if cmd == "H" or cmd == "f":  # Cursor position
            row = (params[0] if params else 1) - 1
            col = (params[1] if len(params) > 1 else 1) - 1
            self.cursor_row = max(0, min(self.rows - 1, row))
            self.cursor_col = max(0, min(self.cols - 1, col))

        elif cmd == "A":  # Cursor up
            n = params[0] if params else 1
            self.cursor_row = max(0, self.cursor_row - n)

        elif cmd == "B":  # Cursor down
            n = params[0] if params else 1
            self.cursor_row = min(self.rows - 1, self.cursor_row + n)

        elif cmd == "C":  # Cursor forward
            n = params[0] if params else 1
            self.cursor_col = min(self.cols - 1, self.cursor_col + n)

        elif cmd == "D":  # Cursor back
            n = params[0] if params else 1
            self.cursor_col = max(0, self.cursor_col - n)

        elif cmd == "J":  # Erase in display
            mode = params[0] if params else 0
            if mode == 0:  # Cursor to end
                self.buffer[self.cursor_row][self.cursor_col:] = [" "] * (self.cols - self.cursor_col)
                for r in range(self.cursor_row + 1, self.rows):
                    self.buffer[r] = self._empty_row()
            elif mode == 1:  # Start to cursor
                for r in range(self.cursor_row):
                    self.buffer[r] = self._empty_row()
                self.buffer[self.cursor_row][:self.cursor_col + 1] = [" "] * (self.cursor_col + 1)
            elif mode == 2 or mode == 3:  # Entire screen
                self.buffer = [self._empty_row() for _ in range(self.rows)]

        elif cmd == "K":  # Erase in line
            mode = params[0] if params else 0
            if mode == 0:  # Cursor to end
                self.buffer[self.cursor_row][self.cursor_col:] = [" "] * (self.cols - self.cursor_col)
            elif mode == 1:  # Start to cursor
                self.buffer[self.cursor_row][:self.cursor_col + 1] = [" "] * (self.cursor_col + 1)
            elif mode == 2:  # Entire line
                self.buffer[self.cursor_row] = self._empty_row()

        elif cmd == "m":  # SGR (colors/attributes) - ignore
            pass

        elif cmd == "r":  # Set scrolling region - ignore for now
            pass

        elif cmd == "s":  # Save cursor
            self.saved_cursor = (self.cursor_row, self.cursor_col)

        elif cmd == "u":  # Restore cursor
            self.cursor_row, self.cursor_col = self.saved_cursor

        elif cmd == "L":  # Insert lines
            n = params[0] if params else 1
            for _ in range(n):
                if self.cursor_row < self.rows:
                    self.buffer.insert(self.cursor_row, self._empty_row())
                    self.buffer.pop()

        elif cmd == "M":  # Delete lines
            n = params[0] if params else 1
            for _ in range(n):
                if self.cursor_row < self.rows:
                    self.buffer.pop(self.cursor_row)
                    self.buffer.append(self._empty_row())

        elif cmd == "P":  # Delete characters
            n = params[0] if params else 1
            row = self.buffer[self.cursor_row]
            del row[self.cursor_col:self.cursor_col + n]
            row.extend([" "] * n)
            self.buffer[self.cursor_row] = row[:self.cols]

        elif cmd == "@":  # Insert characters
            n = params[0] if params else 1
            row = self.buffer[self.cursor_row]
            for _ in range(n):
                row.insert(self.cursor_col, " ")
            self.buffer[self.cursor_row] = row[:self.cols]

        return j, True

    def _line_feed(self):
        """Handle line feed (scroll if at bottom)."""
        if self.cursor_row < self.rows - 1:
            self.cursor_row += 1
        else:
            # Scroll up: save top line to scrollback
            top_line = "".join(self.buffer[0]).rstrip()
            if top_line:
                self.scrollback.append(top_line)
                if len(self.scrollback) > self.max_scrollback:
                    self.scrollback.pop(0)
            self.buffer.pop(0)
            self.buffer.append(self._empty_row())

    def display(self):
        """Get current screen content as list of strings."""
        lines = []
        for row in self.buffer:
            lines.append("".join(row).rstrip())
        # Remove trailing empty lines
        while lines and not lines[-1]:
            lines.pop()
        return lines if lines else [""]

    def display_with_scrollback(self, max_lines=35):
        """Get scrollback + current screen for shell display."""
        screen_lines = self.display()
        # In shell mode, include scrollback for history
        all_lines = self.scrollback[-max_lines:] + screen_lines
        return all_lines[-max_lines:]

    def get_raw_text(self):
        """Get all visible text for context detection."""
        return "\n".join(self.display())


class SSHSession:
    """Manages a single SSH connection for chat terminal."""

    # ─── Context detection patterns ───
    CONTEXT_PATTERNS = {
        "nano": ["GNU nano", "[ New File ]", "[ Read ", "^G Help", "^X Exit"],
        "vim": ["-- INSERT --", "-- VISUAL --", "-- REPLACE --", "~                ", "E37:", "E162:", ":set "],
        "tmux": ["[0]", "[1]", "[2]", "[detached]"],
        "htop": ["htop", "PID USER", "CPU%", "MEM%", "Swp["],
        "top": ["top -", "load average:", "%Cpu(s):", "KiB Mem"],
        "less": ["(END)", "lines ", "byte "],
        "python": [">>> ", "... ", "Python 3", "Python 2"],
        "mysql": ["mysql>", "MariaDB"],
        "confirm": ["[Y/n]", "[y/N]", "(y/n)", "(Y/N)", "yes/no", "Continue?", "[y/n]"],
    }

    # ─── Interactive editor contexts (text goes raw, no newline) ───
    RAW_INPUT_CONTEXTS = {"nano", "nano_exit", "nano_filename", "vim", "python", "mysql"}

    # ─── Context-specific keyboards ───
    CONTEXT_KEYBOARDS = {
        "nano": [
            [("💾 Ctrl+O Save", "ctx:ctrl_o"), ("❌ Ctrl+X Exit", "ctx:ctrl_x")],
            [("🔍 Ctrl+W Search", "ctx:ctrl_w"), ("✂️ Ctrl+K Cut", "ctx:ctrl_k")],
            [("📋 Ctrl+U Paste", "ctx:ctrl_u"), ("🔄 Ctrl+\\ Replace", "ctx:ctrl_bslash")],
        ],
        "nano_exit": [
            [("✅ Y (Save)", "ctx:key_Y"), ("❌ N (Discard)", "ctx:key_N")],
            [("↩️ Ctrl+C Cancel", "ctx:ctrl_c")],
        ],
        "nano_filename": [
            [("⏎ Enter (Confirm)", "ctx:enter")],
            [("↩️ Ctrl+C Cancel", "ctx:ctrl_c")],
        ],
        "vim": [
            [("💾 :wq Save+Quit", "ctx:vim_wq"), ("❌ :q! Force Quit", "ctx:vim_q")],
            [("📝 i Insert", "ctx:key_i"), ("⎋ Esc", "ctx:esc")],
            [("💾 :w Save", "ctx:vim_w"), ("↩️ u Undo", "ctx:key_u")],
        ],
        "tmux": [
            [("🔀 Ctrl+B d Detach", "ctx:tmux_detach"), ("➕ Ctrl+B c New", "ctx:tmux_new")],
            [("◀️ Ctrl+B p Prev", "ctx:tmux_prev"), ("▶️ Ctrl+B n Next", "ctx:tmux_next")],
            [("📋 Ctrl+B w List", "ctx:tmux_list"), ("🔢 Ctrl+B s Sessions", "ctx:tmux_sessions")],
            [("📎 Ctrl+B [ Copy", "ctx:tmux_copy"), ("✂️ Ctrl+B % SplitH", "ctx:tmux_splith")],
        ],
        "htop": [
            [("❌ q Quit", "ctx:key_q"), ("🔍 / Filter", "ctx:key_slash")],
            [("🌳 t Tree", "ctx:key_t"), ("📊 s Sort", "ctx:key_s")],
            [("❓ h Help", "ctx:key_h"), ("🔍 F3 Search", "ctx:key_F3")],
        ],
        "top": [
            [("❌ q Quit", "ctx:key_q"), ("📊 M Mem Sort", "ctx:key_M")],
            [("🔄 P CPU Sort", "ctx:key_P"), ("1 Per-CPU", "ctx:key_1")],
        ],
        "less": [
            [("❌ q Quit", "ctx:key_q"), ("🔍 / Search", "ctx:key_slash")],
            [("⬇️ Space Next", "ctx:key_space"), ("⬆️ b Back", "ctx:key_b")],
            [("⬇️ G End", "ctx:key_G"), ("⬆️ g Start", "ctx:key_gg")],
        ],
        "python": [
            [("❌ exit()", "ctx:py_exit"), ("⛔ Ctrl+D", "ctx:ctrl_d")],
            [("⛔ Ctrl+C", "ctx:ctrl_c"), ("⏎ Enter", "ctx:enter")],
        ],
        "mysql": [
            [("❌ exit", "ctx:mysql_exit"), ("📋 show databases;", "ctx:mysql_showdb")],
            [("📋 show tables;", "ctx:mysql_showtbl"), ("⏎ Enter", "ctx:enter")],
        ],
        "confirm": [
            [("✅ Y", "ctx:key_y"), ("❌ N", "ctx:key_n")],
            [("⛔ Ctrl+C Cancel", "ctx:ctrl_c")],
        ],
    }

    # ─── Callback → byte mapping ───
    CTX_MAP = {
        # Control chars
        "ctrl_a": "\x01", "ctrl_b": "\x02", "ctrl_c": "\x03",
        "ctrl_d": "\x04", "ctrl_k": "\x0b", "ctrl_o": "\x0f",
        "ctrl_u": "\x15", "ctrl_w": "\x17", "ctrl_x": "\x18",
        "ctrl_z": "\x1a", "ctrl_bslash": "\x1c",
        "esc": "\x1b", "tab": "\t", "enter": "\r",
        # Arrow keys
        "arrow_up": "\x1b[A", "arrow_down": "\x1b[B",
        "arrow_right": "\x1b[C", "arrow_left": "\x1b[D",
        # Function keys
        "key_F3": "\x1bOR",
        # Single keys
        "key_Y": "Y", "key_N": "N", "key_y": "y", "key_n": "n",
        "key_i": "i", "key_q": "q", "key_t": "t", "key_s": "s",
        "key_b": "b", "key_h": "h", "key_u": "u",
        "key_M": "M", "key_P": "P", "key_G": "G", "key_1": "1",
        "key_slash": "/", "key_space": " ",
        # Multi-char sequences
        "key_gg": "gg",
        "vim_wq": "\x1b:wq\r", "vim_q": "\x1b:q!\r", "vim_w": "\x1b:w\r",
        "py_exit": "exit()\r",
        "mysql_exit": "exit\r", "mysql_showdb": "show databases;\r",
        "mysql_showtbl": "show tables;\r",
        # Tmux sequences (Ctrl+B prefix)
        "tmux_detach": "\x02d", "tmux_new": "\x02c",
        "tmux_next": "\x02n", "tmux_prev": "\x02p",
        "tmux_list": "\x02w", "tmux_sessions": "\x02s",
        "tmux_copy": "\x02[", "tmux_splith": "\x02%",
    }

    def __init__(self, user_id: int, server: dict, bot, chat_id: int, message_id: int):
        self.user_id = user_id
        self.server = server
        self.bot = bot
        self.chat_id = chat_id
        self.message_id = message_id  # the terminal display message
        self.conn = None
        self.process = None
        self.output_buffer = ""
        self.buffer_lock = asyncio.Lock()
        self.update_task = None
        self.alive = False
        self.last_edit = 0
        self._manual_disconnect = False
        # ─── output mode & context ───
        self.output_mode = "stream"  # "stream" or "log"
        self.detected_context = "default"
        self._context_lock_until = 0  # state machine lock timestamp
        self.log_sent_index = 0
        self.full_output_lines = []  # for log mode
        # ─── VT100 screen buffer for proper rendering ───
        self.screen = VT100Screen(80, 24)
        self.output_lines = []  # derived from screen for display

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
            if not self._manual_disconnect:
                try:
                    await self._flush_and_update()
                    await self._update_terminal_message(disconnected=True)
                except Exception:
                    pass  # Python may be shutting down

    async def _output_loop(self):
        """Periodically flush buffer and edit the terminal message."""
        try:
            while self.alive:
                interval = LOG_BUFFER_SEC if self.output_mode == "log" else OUTPUT_BUFFER_SEC
                await asyncio.sleep(interval)
                await self._flush_and_update()
        except (asyncio.CancelledError, Exception):
            pass

    async def _flush_and_update(self):
        """Process buffer through VT100 screen and update message."""
        async with self.buffer_lock:
            if not self.output_buffer:
                return
            raw = self.output_buffer
            self.output_buffer = ""

        # Feed raw data through VT100 screen buffer (handles cursor positioning)
        self.screen.feed(raw)

        # Get properly rendered screen lines
        self.output_lines = self.screen.display_with_scrollback(MAX_LINES)

        # For log mode, track new output using a simple ANSI-stripped version
        clean = self._strip_ansi(raw)
        new_log_lines = clean.split("\n")
        for line in new_log_lines:
            line = line.replace("\r", "").strip()
            if line:
                self.full_output_lines.append(line)

        # Detect context from screen content
        self._detect_context()

        # Cap full_output_lines to prevent unbounded memory growth
        MAX_FULL_LINES = 5000
        if len(self.full_output_lines) > MAX_FULL_LINES:
            overflow = len(self.full_output_lines) - MAX_FULL_LINES
            self.full_output_lines = self.full_output_lines[-MAX_FULL_LINES:]
            self.log_sent_index = max(0, self.log_sent_index - overflow)

        if self.output_mode == "log":
            await self._send_log_chunk()
            await self._update_terminal_message()
        else:
            await self._update_terminal_message()

    def _strip_ansi(self, text):
        """Remove ANSI escape sequences (for log mode text)."""
        text = re.sub(r'\x1b\[[0-9;?]*[a-zA-Z]', '', text)
        text = re.sub(r'\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)', '', text)
        text = re.sub(r'\x1b[()][0-9A-B]', '', text)
        text = re.sub(r'\x1b[=>78]', '', text)
        return text

    def _detect_context(self):
        """Detect running program from screen content."""
        # Skip detection if context was recently set by state machine
        if time.time() < self._context_lock_until:
            return

        # Use screen's rendered lines for accurate detection
        screen_lines = self.screen.display()
        all_text = "\n".join(screen_lines)
        # Bottom area of screen (where status/prompts appear)
        bottom_lines = screen_lines[-5:] if len(screen_lines) >= 5 else screen_lines
        bottom_text = "\n".join(bottom_lines)

        # ── Nano state machine (ordered by priority) ──
        # 1) nano filename prompt: "File Name to Write:" after pressing Y
        for line in bottom_lines:
            if "File Name to Write:" in line or "file name to write:" in line.lower():
                self.detected_context = "nano_filename"
                return

        # 2) nano exit prompt: "Save modified buffer?" after pressing Ctrl+X
        for line in bottom_lines:
            if "Save modified buffer" in line or "save modified buffer" in line:
                self.detected_context = "nano_exit"
                return

        # 3) nano editor is open
        for pattern in self.CONTEXT_PATTERNS["nano"]:
            if pattern in all_text:
                self.detected_context = "nano"
                return

        # ── Check confirm prompts (high priority) ──
        for pattern in self.CONTEXT_PATTERNS["confirm"]:
            if pattern.lower() in bottom_text.lower():
                self.detected_context = "confirm"
                return

        # ── Check all other contexts ──
        for ctx, patterns in self.CONTEXT_PATTERNS.items():
            if ctx in ("confirm", "nano"):
                continue
            for pattern in patterns:
                if pattern in all_text:
                    self.detected_context = ctx
                    return

        # ── Default (shell) ──
        self.detected_context = "default"

    def _build_keyboard(self, disconnected=False):
        """Build context-aware keyboard."""
        srv = self.server

        if disconnected:
            return [[
                InlineKeyboardButton("🔄 Reconnect", callback_data=f"reconnect:{srv['id']}"),
                InlineKeyboardButton("🏠 Home", callback_data="menu:main"),
            ]]

        # Mode toggle button
        if self.output_mode == "stream":
            mode_btn = InlineKeyboardButton("📜 Log", callback_data="term:mode:log")
        else:
            mode_btn = InlineKeyboardButton("📺 Stream", callback_data="term:mode:stream")

        # Context-specific buttons
        ctx = self.detected_context
        if ctx in self.CONTEXT_KEYBOARDS:
            keyboard = []
            for row in self.CONTEXT_KEYBOARDS[ctx]:
                kb_row = []
                for label, cb_data in row:
                    kb_row.append(InlineKeyboardButton(label, callback_data=cb_data))
                keyboard.append(kb_row)
            # Common controls row
            keyboard.append([
                mode_btn,
                InlineKeyboardButton("🧹 Clear", callback_data="term:clear"),
                InlineKeyboardButton("⏹ DC", callback_data="term:disconnect"),
            ])
            return keyboard

        # Default keyboard (shell mode)
        return [
            [
                InlineKeyboardButton("⏎ Enter", callback_data="ctx:enter"),
                InlineKeyboardButton("⛔ Ctrl+C", callback_data="ctx:ctrl_c"),
                InlineKeyboardButton("⌛ Ctrl+Z", callback_data="ctx:ctrl_z"),
            ],
            [
                InlineKeyboardButton("⬆️", callback_data="ctx:arrow_up"),
                InlineKeyboardButton("⬇️", callback_data="ctx:arrow_down"),
                InlineKeyboardButton("↹ Tab", callback_data="ctx:tab"),
                InlineKeyboardButton("Ctrl+D", callback_data="ctx:ctrl_d"),
            ],
            [
                mode_btn,
                InlineKeyboardButton("🧹 Clear", callback_data="term:clear"),
                InlineKeyboardButton("⏹ DC", callback_data="term:disconnect"),
            ],
        ]

    async def _send_log_chunk(self):
        """Send new output as a separate message (log mode)."""
        total = len(self.full_output_lines)
        if total <= self.log_sent_index:
            return

        new_lines = self.full_output_lines[self.log_sent_index:total]
        self.log_sent_index = total

        # Build chunk text, skip if only whitespace
        chunk_text = "\n".join(new_lines).strip()
        if not chunk_text:
            return

        # Split into parts if too long
        parts = []
        current = ""
        for line in new_lines:
            candidate = current + "\n" + line if current else line
            if len(candidate) > MAX_MSG_LEN - 150:
                if current.strip():
                    parts.append(current)
                current = line
            else:
                current = candidate
        if current.strip():
            parts.append(current)

        for part in parts:
            escaped = html.escape(part.strip())
            if not escaped:
                continue
            try:
                await self.bot.send_message(
                    chat_id=self.chat_id,
                    text=f"<pre>{escaped}</pre>",
                    parse_mode="HTML",
                )
            except Exception as e:
                # If HTML parse fails, try plain text
                try:
                    await self.bot.send_message(
                        chat_id=self.chat_id,
                        text=part.strip()[:MAX_MSG_LEN],
                    )
                except Exception:
                    pass

    async def _update_terminal_message(self, disconnected=False):
        """Edit the terminal message with current output."""
        now = time.time()

        # Rate limit: don't edit more than once per second
        if not disconnected and (now - self.last_edit) < 1.0:
            return

        srv = self.server
        label = srv.get("label") or srv["host"]

        if disconnected:
            status_line = "🔴 Disconnected"
        else:
            ctx_name = self.detected_context
            ctx_display = {
                "nano": "📝 nano", "nano_exit": "📝 nano ⚠️ save?",
                "nano_filename": "📝 nano 📄 filename?",
                "vim": "📝 vim", "tmux": "🔲 tmux",
                "htop": "📊 htop", "top": "📊 top",
                "less": "📄 less", "python": "🐍 python",
                "mysql": "🗄 mysql", "confirm": "❓ confirm",
            }.get(ctx_name, "")
            mode_icon = "📺" if self.output_mode == "stream" else "📜"
            status_line = f"🟢 Connected  {mode_icon} {ctx_display}".strip()

        if self.output_mode == "log" and not disconnected:
            # In log mode, show last few lines as preview + buttons
            preview_lines = self.output_lines[-5:] if self.output_lines else [""]
            preview_text = "\n".join(preview_lines).strip() or "(waiting for output...)"
            if len(preview_text) > 500:
                preview_text = preview_text[-500:]
            terminal_text = html.escape(preview_text)
            log_info = f"\n📜 <i>Log mode — new output sent as messages</i>"
        else:
            lines = self.output_lines[-MAX_LINES:] if self.output_lines else [""]
            terminal_text = "\n".join(lines)
            if len(terminal_text) > MAX_MSG_LEN - 200:
                terminal_text = terminal_text[-(MAX_MSG_LEN - 200):]
            terminal_text = html.escape(terminal_text)
            log_info = ""

        msg = (
            f"🖥 <b>Terminal — {html.escape(label)}</b>\n"
            f"<code>{srv['username']}@{srv['host']}:{srv.get('port', 22)}</code>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"<pre>{terminal_text}</pre>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"{status_line}{log_info}"
        )

        keyboard = self._build_keyboard(disconnected)

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
        """Send user input to SSH, context-aware."""
        if self.process and self.alive:
            ctx = self.detected_context
            if ctx in self.RAW_INPUT_CONTEXTS:
                # In editors/interactive apps: send text raw (typed into buffer)
                self.process.stdin.write(text)
            else:
                # In shell: send as command with carriage return (Enter key)
                self.process.stdin.write(text + "\r")

    async def send_raw(self, data):
        """Send raw data to SSH (no newline appended)."""
        if self.process and self.alive:
            self.process.stdin.write(data)

    async def disconnect(self):
        """Close the SSH session."""
        self._manual_disconnect = True
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


# ─── SFTP Chat Functions ───

async def sftp_connect(uid, server, bot, chat_id, msg_id):
    """Connect SFTP and store session."""
    srv = server
    port = int(srv.get("port", 22))
    connect_kwargs = {
        "host": srv["host"], "port": port,
        "username": srv["username"], "known_hosts": None,
    }
    if srv.get("auth_type") == "key":
        key_data = srv.get("private_key", "")
        passphrase = srv.get("passphrase") or None
        pkey = asyncssh.import_private_key(key_data, passphrase)
        connect_kwargs["client_keys"] = [pkey]
    else:
        connect_kwargs["password"] = srv.get("password", "")

    conn = await asyncio.wait_for(asyncssh.connect(**connect_kwargs), timeout=15)
    client = await conn.start_sftp_client()
    try:
        home = await client.getcwd()
    except Exception:
        home = "/"

    sftp_sessions[uid] = {
        "conn": conn, "client": client, "path": home,
        "files": [], "page": 0, "server": srv,
        "chat_id": chat_id, "msg_id": msg_id,
        "awaiting_mkdir": False,
    }
    await sftp_list(uid, bot)


async def sftp_close(uid):
    """Close SFTP session."""
    sess = sftp_sessions.pop(uid, None)
    if sess:
        try:
            sess["client"].exit()
        except Exception:
            pass
        try:
            sess["conn"].close()
        except Exception:
            pass


async def sftp_list(uid, bot):
    """List current directory and send as message with buttons."""
    sess = sftp_sessions.get(uid)
    if not sess:
        return
    client = sess["client"]
    path = sess["path"]

    try:
        entries = await client.readdir(path)
    except Exception as e:
        await bot.edit_message_text(
            f"❌ Error: {html.escape(str(e))}",
            chat_id=sess["chat_id"], message_id=sess["msg_id"],
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Back", callback_data="sf:close")]
            ]),
        )
        return

    # Sort: dirs first, then files
    items = []
    for e in entries:
        name = e.filename
        if name in (".", ".."):
            continue
        perms = e.attrs.permissions
        is_dir = bool(perms and stat_mod.S_ISDIR(perms)) if perms else False
        size = e.attrs.size or 0
        items.append({"name": name, "is_dir": is_dir, "size": size})
    items.sort(key=lambda x: (not x["is_dir"], x["name"].lower()))
    sess["files"] = items

    page = sess["page"]
    total_pages = max(1, (len(items) + SFTP_PAGE_SIZE - 1) // SFTP_PAGE_SIZE)
    page = min(page, total_pages - 1)
    sess["page"] = page
    start = page * SFTP_PAGE_SIZE
    page_items = items[start:start + SFTP_PAGE_SIZE]

    label = sess["server"].get("label") or sess["server"]["host"]
    text = (
        f"📂 <b>SFTP</b> — {html.escape(label)}\n"
        f"<code>{html.escape(path)}</code>\n"
        f"📁 {len([x for x in items if x['is_dir']])} folders · "
        f"📄 {len([x for x in items if not x['is_dir']])} files"
    )
    if total_pages > 1:
        text += f" · 📖 {page + 1}/{total_pages}"

    rows = []
    for i, f in enumerate(page_items):
        idx = start + i
        icon = "📁" if f["is_dir"] else _file_icon(f["name"])
        size_str = "" if f["is_dir"] else f" ({_human_size(f['size'])})"
        btn_text = f"{icon} {f['name']}{size_str}"
        if len(btn_text) > 40:
            btn_text = btn_text[:37] + "..."
        if f["is_dir"]:
            rows.append([InlineKeyboardButton(btn_text, callback_data=f"sf:cd:{idx}")])
        else:
            rows.append([
                InlineKeyboardButton(btn_text, callback_data=f"sf:info:{idx}"),
            ])

    # Navigation row
    nav = []
    nav.append(InlineKeyboardButton("⬆️ Up", callback_data="sf:up"))
    if page > 0:
        nav.append(InlineKeyboardButton("◀️", callback_data=f"sf:pg:{page - 1}"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton("▶️", callback_data=f"sf:pg:{page + 1}"))
    rows.append(nav)

    # Action row
    rows.append([
        InlineKeyboardButton("📤 Upload", callback_data="sf:upload"),
        InlineKeyboardButton("📁 New Folder", callback_data="sf:mkdir"),
    ])
    rows.append([
        InlineKeyboardButton("❌ Close", callback_data="sf:close"),
    ])

    try:
        await bot.edit_message_text(
            text, chat_id=sess["chat_id"], message_id=sess["msg_id"],
            parse_mode="HTML", reply_markup=InlineKeyboardMarkup(rows),
        )
    except Exception:
        pass


def _file_icon(name):
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    icons = {
        "py": "🐍", "go": "🔷", "js": "📄", "ts": "📄", "sh": "⚙️",
        "json": "📋", "yml": "📋", "yaml": "📋", "md": "📝", "txt": "📝",
        "log": "📝", "html": "🌐", "css": "🎨", "jpg": "🖼", "jpeg": "🖼",
        "png": "🖼", "gif": "🖼", "svg": "🖼", "mp4": "🎬", "mp3": "🎵",
        "zip": "📦", "tar": "📦", "gz": "📦", "pdf": "📕", "conf": "⚙️",
        "env": "🔒", "key": "🔑", "pem": "🔑",
    }
    return icons.get(ext, "📄")


def _human_size(size):
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.0f}{unit}" if unit == "B" else f"{size:.1f}{unit}"
        size /= 1024
    return f"{size:.1f}TB"


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

    # Force refresh membership status on /start
    invalidate_membership_cache(user.id)
    if not await require_membership(update, context):
        return

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

    # ─── Membership recheck button ───
    if data == "check_membership":
        invalidate_membership_cache(uid)
        if await is_channel_member(context.bot, uid):
            await query.edit_message_text(
                f"✅ <b>عضویت تایید شد!</b>\n\n"
                f"👋 Welcome to EazySSH!\n"
                f"Choose your preferred terminal:",
                parse_mode="HTML",
                reply_markup=main_menu_keyboard(),
            )
        else:
            await query.answer("❌ هنوز عضو کانال نیستید!", show_alert=True)
        return

    # ─── Check channel membership for all other actions ───
    # Invalidate cache to ensure fresh check on every button press
    invalidate_membership_cache(uid)
    if not await require_membership(update, context):
        return

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

    # ─── Reconnect ───
    if data.startswith("reconnect:"):
        server_id = data.split(":", 1)[1]
        # Fall through to connect logic below
        data = f"srv:connect:{server_id}"

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
            session = active_sessions[uid]
            session.output_lines = []
            session.full_output_lines = []
            session.log_sent_index = 0
            session.screen = VT100Screen(80, 24)
            await session._update_terminal_message()
        return

    # ─── Mode toggle ───
    if data.startswith("term:mode:"):
        new_mode = data.split(":", 2)[2]
        if uid in active_sessions:
            session = active_sessions[uid]
            old_mode = session.output_mode
            session.output_mode = new_mode
            if new_mode == "log":
                # Start log from current position (don't re-send old output)
                session.log_sent_index = len(session.full_output_lines)
            elif new_mode == "stream" and old_mode == "log":
                # Switching back to stream: refresh display immediately
                pass
            await session._update_terminal_message()
        return

    # ─── Context-aware buttons ───
    if data.startswith("ctx:"):
        if uid not in active_sessions:
            return
        session = active_sessions[uid]
        action = data[4:]  # strip "ctx:" prefix
        raw = SSHSession.CTX_MAP.get(action)
        if raw:
            await session.send_raw(raw)

            # ── State machine: immediate context transitions ──
            ctx = session.detected_context

            if ctx == "nano" and action == "ctrl_x":
                # Ctrl+X in nano → "Save modified buffer?" prompt
                session.detected_context = "nano_exit"
                session._context_lock_until = time.time() + 2.0
                await session._update_terminal_message()

            elif ctx == "nano_exit" and action == "key_Y":
                # Y in nano exit → "File Name to Write:" prompt
                session.detected_context = "nano_filename"
                session._context_lock_until = time.time() + 2.0
                await session._update_terminal_message()

            elif ctx == "nano_exit" and action == "key_N":
                # N in nano exit → nano closes, back to shell
                session.detected_context = "default"
                session._context_lock_until = time.time() + 1.0
                # Wait for shell prompt then update
                async def _delayed_update():
                    await asyncio.sleep(0.5)
                    if session.alive:
                        await session._flush_and_update()
                asyncio.create_task(_delayed_update())

            elif ctx == "nano_exit" and action == "ctrl_c":
                # Cancel exit → back to nano editor
                session.detected_context = "nano"
                session._context_lock_until = time.time() + 2.0
                await session._update_terminal_message()

            elif ctx == "nano_filename" and action == "enter":
                # Enter on filename → nano saves and exits
                session.detected_context = "default"
                session._context_lock_until = time.time() + 1.0
                async def _delayed_update():
                    await asyncio.sleep(0.5)
                    if session.alive:
                        await session._flush_and_update()
                asyncio.create_task(_delayed_update())

            elif ctx == "nano_filename" and action == "ctrl_c":
                # Cancel filename → back to nano editor
                session.detected_context = "nano"
                session._context_lock_until = time.time() + 2.0
                await session._update_terminal_message()

            elif ctx == "nano" and action == "ctrl_o":
                # Ctrl+O in nano → "File Name to Write:" prompt
                session.detected_context = "nano_filename"
                session._context_lock_until = time.time() + 2.0
                await session._update_terminal_message()

            else:
                # Generic: schedule delayed update for other contexts
                async def _delayed_update():
                    await asyncio.sleep(0.5)
                    if session.alive:
                        await session._flush_and_update()
                asyncio.create_task(_delayed_update())

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

    # ─── SFTP Chat ───
    if data.startswith("srv:sftp:"):
        server_id = data.split(":", 2)[2]
        srv = find_server(uid, server_id)
        if not srv:
            return
        # Close any existing SFTP session
        await sftp_close(uid)
        label = srv.get("label") or srv["host"]
        await query.edit_message_text(
            f"📂 Connecting SFTP to <b>{html.escape(label)}</b>...",
            parse_mode="HTML",
        )
        try:
            await sftp_connect(uid, srv, query.get_bot(), query.message.chat_id, query.message.message_id)
        except Exception as e:
            err_msg = str(e) or traceback.format_exc().split("\n")[-2]
            await query.edit_message_text(
                f"❌ SFTP connection failed:\n<code>{html.escape(err_msg)}</code>",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 Back", callback_data="menu:chat_terminal")],
                ]),
            )
        return

    # ─── SFTP: Navigate into folder ───
    if data.startswith("sf:cd:"):
        sess = sftp_sessions.get(uid)
        if not sess:
            return
        idx = int(data.split(":")[2])
        if idx < len(sess["files"]):
            f = sess["files"][idx]
            if f["is_dir"]:
                new_path = sess["path"].rstrip("/") + "/" + f["name"]
                sess["path"] = new_path
                sess["page"] = 0
                await sftp_list(uid, query.get_bot())
        return

    # ─── SFTP: Go up ───
    if data == "sf:up":
        sess = sftp_sessions.get(uid)
        if not sess:
            return
        path = sess["path"]
        parent = path.rsplit("/", 1)[0] or "/"
        sess["path"] = parent
        sess["page"] = 0
        await sftp_list(uid, query.get_bot())
        return

    # ─── SFTP: Pagination ───
    if data.startswith("sf:pg:"):
        sess = sftp_sessions.get(uid)
        if not sess:
            return
        sess["page"] = int(data.split(":")[2])
        await sftp_list(uid, query.get_bot())
        return

    # ─── SFTP: File info (download/delete) ───
    if data.startswith("sf:info:"):
        sess = sftp_sessions.get(uid)
        if not sess:
            return
        idx = int(data.split(":")[2])
        if idx < len(sess["files"]):
            f = sess["files"][idx]
            full_path = sess["path"].rstrip("/") + "/" + f["name"]
            icon = _file_icon(f["name"])
            await query.edit_message_text(
                f"{icon} <b>{html.escape(f['name'])}</b>\n"
                f"📏 {_human_size(f['size'])}\n"
                f"📍 <code>{html.escape(full_path)}</code>",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton("📥 Download", callback_data=f"sf:dl:{idx}"),
                        InlineKeyboardButton("🗑 Delete", callback_data=f"sf:rm:{idx}"),
                    ],
                    [InlineKeyboardButton("🔙 Back", callback_data="sf:back")],
                ]),
            )
        return

    # ─── SFTP: Download file ───
    if data.startswith("sf:dl:"):
        sess = sftp_sessions.get(uid)
        if not sess:
            return
        idx = int(data.split(":")[2])
        if idx < len(sess["files"]):
            f = sess["files"][idx]
            full_path = sess["path"].rstrip("/") + "/" + f["name"]
            if f["size"] > 50 * 1024 * 1024:
                await query.answer("❌ File too large (max 50MB)", show_alert=True)
                return
            await query.answer("📥 Downloading...")
            try:
                tmp = tempfile.NamedTemporaryFile(delete=False, suffix="_" + f["name"])
                tmp_path = tmp.name
                tmp.close()
                await sess["client"].get(full_path, tmp_path)
                with open(tmp_path, "rb") as fh:
                    await query.get_bot().send_document(
                        chat_id=sess["chat_id"],
                        document=fh,
                        filename=f["name"],
                        caption=f"📥 <code>{html.escape(full_path)}</code>",
                        parse_mode="HTML",
                    )
                os.unlink(tmp_path)
            except Exception as e:
                await query.get_bot().send_message(
                    chat_id=sess["chat_id"],
                    text=f"❌ Download failed: {html.escape(str(e))}",
                )
        return

    # ─── SFTP: Delete file ───
    if data.startswith("sf:rm:"):
        sess = sftp_sessions.get(uid)
        if not sess:
            return
        idx = int(data.split(":")[2])
        if idx < len(sess["files"]):
            f = sess["files"][idx]
            full_path = sess["path"].rstrip("/") + "/" + f["name"]
            try:
                if f["is_dir"]:
                    await sess["client"].rmtree(full_path)
                else:
                    await sess["client"].remove(full_path)
            except Exception as e:
                await query.answer(f"❌ {e}", show_alert=True)
                return
            await sftp_list(uid, query.get_bot())
        return

    # ─── SFTP: Back to listing ───
    if data == "sf:back":
        sess = sftp_sessions.get(uid)
        if sess:
            await sftp_list(uid, query.get_bot())
        return

    # ─── SFTP: Mkdir prompt ───
    if data == "sf:mkdir":
        sess = sftp_sessions.get(uid)
        if not sess:
            return
        sess["awaiting_mkdir"] = True
        await query.edit_message_text(
            f"📁 Send the folder name to create in:\n"
            f"<code>{html.escape(sess['path'])}</code>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("❌ Cancel", callback_data="sf:back")],
            ]),
        )
        return

    # ─── SFTP: Upload prompt ───
    if data == "sf:upload":
        sess = sftp_sessions.get(uid)
        if not sess:
            return
        sess["awaiting_upload"] = True
        await query.edit_message_text(
            f"📤 Send a file to upload to:\n"
            f"<code>{html.escape(sess['path'])}</code>\n\n"
            f"Max 50MB.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("❌ Cancel", callback_data="sf:back")],
            ]),
        )
        return

    # ─── SFTP: Close ───
    if data == "sf:close":
        await sftp_close(uid)
        await query.edit_message_text(
            "📂 SFTP session closed.",
            reply_markup=main_menu_keyboard(),
        )
        return


# ─── Message Handler (text input) ───

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    text = update.message.text or ""

    # ─── Allow admin broadcast without membership check ───
    if uid == ADMIN_ID and ADMIN_ID in broadcast_pending:
        pass  # skip membership for admin broadcast
    else:
        # ─── Check channel membership ───
        if not await require_membership(update, context):
            return

    # ─── SFTP: File upload (document received) ───
    sess = sftp_sessions.get(uid)
    if sess and sess.get("awaiting_upload") and update.message.document:
        sess["awaiting_upload"] = False
        doc = update.message.document
        if doc.file_size > 50 * 1024 * 1024:
            await update.message.reply_text("❌ File too large (max 50MB)")
            await sftp_list(uid, context.bot)
            return
        try:
            tg_file = await doc.get_file()
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix="_" + doc.file_name)
            tmp_path = tmp.name
            tmp.close()
            await tg_file.download_to_drive(tmp_path)
            remote_path = sess["path"].rstrip("/") + "/" + doc.file_name
            await sess["client"].put(tmp_path, remote_path)
            os.unlink(tmp_path)
            try:
                await update.message.delete()
            except Exception:
                pass
        except Exception as e:
            await update.message.reply_text(f"❌ Upload failed: {e}")
        await sftp_list(uid, context.bot)
        return

    # ─── SFTP: mkdir response ───
    sess = sftp_sessions.get(uid)
    if sess and sess.get("awaiting_mkdir") and text:
        sess["awaiting_mkdir"] = False
        folder_name = text.strip()
        if folder_name:
            full_path = sess["path"].rstrip("/") + "/" + folder_name
            try:
                await sess["client"].mkdir(full_path)
            except Exception as e:
                await update.message.reply_text(f"❌ {e}")
        try:
            await update.message.delete()
        except Exception:
            pass
        await sftp_list(uid, context.bot)
        return

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
            # Schedule update for context-aware button refresh
            async def _delayed_update():
                await asyncio.sleep(0.8)
                if session.alive:
                    await session._flush_and_update()
            asyncio.create_task(_delayed_update())
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


# ─── Membership Enforcement Background Task ───

async def membership_enforcement_loop(bot):
    """Periodically check active users. If they left the channel, kick them."""
    while True:
        try:
            await asyncio.sleep(20)  # check every 20 seconds

            # Check all users with active SSH sessions
            active_uids = list(active_sessions.keys())
            for uid in active_uids:
                if uid == ADMIN_ID:
                    continue
                invalidate_membership_cache(uid)
                if not await is_channel_member(bot, uid):
                    # User left → kill session
                    try:
                        session = active_sessions.pop(uid, None)
                        if session:
                            await session.disconnect()
                        await bot.send_message(
                            chat_id=uid,
                            text=(
                                f"🔒 <b>دسترسی قطع شد</b>\n\n"
                                f"شما از کانال {REQUIRED_CHANNEL} خارج شده‌اید.\n"
                                f"برای استفاده مجدد از ربات، دوباره عضو شوید و /start بزنید."
                            ),
                            parse_mode="HTML",
                            reply_markup=join_channel_keyboard(),
                        )
                    except Exception:
                        pass

            # Check SFTP sessions
            sftp_uids = list(sftp_sessions.keys())
            for uid in sftp_uids:
                if uid == ADMIN_ID:
                    continue
                if not await is_channel_member(bot, uid):
                    try:
                        await sftp_close(uid)
                    except Exception:
                        pass
        except Exception as e:
            print(f"Membership enforcement error: {e}")


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

        # Start background membership enforcement
        asyncio.create_task(membership_enforcement_loop(application.bot))
        print(f"Channel enforcement enabled for: {REQUIRED_CHANNEL}")

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
