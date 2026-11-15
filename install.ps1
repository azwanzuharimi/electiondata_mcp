$ErrorActionPreference = "Stop"

$Source = if ($env:ELECTIONDATA_MCP_SOURCE) { $env:ELECTIONDATA_MCP_SOURCE } else { "https://github.com/azwanzuharimi/electiondata_mcp/archive/refs/heads/main.zip" }

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "Installing uv (Python package manager from Astral)..."
    powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
    $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
}

Write-Host "Installing electiondata-mcp..."
uv tool install --force --python 3.12 $Source
if ($LASTEXITCODE -ne 0) { throw "uv tool install failed" }

$Bin = (uv tool dir --bin).Trim()
$Exe = Join-Path $Bin "electiondata-mcp.exe"
& $Exe --check
if ($LASTEXITCODE -ne 0) { throw "electiondata-mcp --check failed" }

$Json = $Exe.Replace("\", "\\")
Write-Host @"

Installed: $Exe

Connect to Claude Code:
  claude mcp add electiondata -- "$Exe"

Connect to Claude Desktop: add this to claude_desktop_config.json, then restart Claude Desktop:
  "electiondata": { "command": "$Json" }

Serve over HTTP for web chat apps:
  & "$Exe" --http --host 0.0.0.0 --port 8000

Optional, for live turnout, seat lineage and party history: get a free key at
https://electiondata.my/console/ and set ELECTIONDATA_API_KEY.

If 'electiondata-mcp' is not found in a new terminal, run: uv tool update-shell
"@
