#!/bin/sh
set -eu

SOURCE="${ELECTIONDATA_MCP_SOURCE:-https://github.com/azwanzuharimi/electiondata_mcp/archive/refs/heads/main.zip}"

if ! command -v uv >/dev/null 2>&1; then
    echo "Installing uv (Python package manager from Astral)..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    PATH="$HOME/.local/bin:$PATH"
    export PATH
fi

echo "Installing electiondata-mcp..."
uv tool install --force --python 3.12 "$SOURCE"

BIN="$(uv tool dir --bin)"
"$BIN/electiondata-mcp" --check

cat <<EOF

Installed: $BIN/electiondata-mcp

Connect to Claude Code:
  claude mcp add electiondata -- "$BIN/electiondata-mcp"

Connect to Claude Desktop: add this to claude_desktop_config.json, then restart Claude Desktop:
  "electiondata": { "command": "$BIN/electiondata-mcp" }

Serve over HTTP for web chat apps:
  "$BIN/electiondata-mcp" --http --host 0.0.0.0 --port 8000

Optional, for live turnout, seat lineage and party history: get a free key at
https://electiondata.my/console/ and set ELECTIONDATA_API_KEY.

If 'electiondata-mcp' is not found in a new terminal, run: uv tool update-shell
EOF
