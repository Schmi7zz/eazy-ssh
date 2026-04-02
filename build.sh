#!/bin/bash
# ─── EazySSH Build — Pre-compile JSX ───
# Removes babel-standalone (~1.1MB) from runtime loading
# Usage: bash build.sh [path_to_index.html]
set -e

HTML="${1:-/opt/ssh-terminal/frontend/index.html}"

if [ ! -f "$HTML" ]; then
    echo "❌ File not found: $HTML"; exit 1
fi

echo "🔧 EazySSH Build — Pre-compile JSX"

# Install node if needed
if ! command -v node &>/dev/null; then
    echo "📦 Installing Node.js..."
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash - >/dev/null 2>&1
    apt-get install -y nodejs >/dev/null 2>&1
fi

# Install babel if needed
if ! npx @babel/core --version &>/dev/null 2>&1; then
    echo "📦 Installing Babel..."
    npm install -g @babel/core @babel/cli @babel/preset-react >/dev/null 2>&1
fi

TMPJSX=$(mktemp /tmp/eazy-XXXXXX.jsx)
TMPJS=$(mktemp /tmp/eazy-XXXXXX.js)

# Extract JSX source
sed -n '/SCRIPT_TYPE_END/,/<\/script>/{/SCRIPT_TYPE_END/d;/<\/script>/d;p}' "$HTML" > "$TMPJSX"

if [ ! -s "$TMPJSX" ]; then
    echo "❌ No JSX found. Already compiled or wrong format."
    rm -f "$TMPJSX" "$TMPJS"; exit 1
fi

echo "⚙️  Compiling JSX → JavaScript..."
npx babel --presets @babel/preset-react "$TMPJSX" -o "$TMPJS" 2>/dev/null

if [ ! -s "$TMPJS" ]; then
    echo "❌ Compilation failed."
    rm -f "$TMPJSX" "$TMPJS"; exit 1
fi

echo "📝 Patching index.html..."
cp "$HTML" "${HTML}.bak"

# Remove babel script tag
sed -i 's|<!-- BABEL_SCRIPT_START -->.*<!-- BABEL_SCRIPT_END -->||' "$HTML"

# Change text/babel to regular script
sed -i 's|<!-- SCRIPT_TYPE_START --><script type="text/babel"><!-- SCRIPT_TYPE_END -->|<script>|' "$HTML"

# Replace JSX with compiled JS
START=$(grep -n '^<script>$' "$HTML" | tail -1 | cut -d: -f1)
END=$(( $(grep -n '</script>' "$HTML" | tail -2 | head -1 | cut -d: -f1) ))

head -n "$START" "$HTML" > "${HTML}.tmp"
cat "$TMPJS" >> "${HTML}.tmp"
tail -n +"$END" "$HTML" >> "${HTML}.tmp"
mv "${HTML}.tmp" "$HTML"

rm -f "$TMPJSX" "$TMPJS"

echo ""
echo "✅ Done! babel-standalone (~1.1MB) removed from runtime."
echo "   Backup: ${HTML}.bak"
echo "   Undo:   cp ${HTML}.bak $HTML"
