<p align="left">
  <a href="README_en.md"><img src="https://img.shields.io/badge/English Mode-blue.svg" alt="English"></a>
  <a href="README.md"><img src="https://img.shields.io/badge/日本語 モード-red.svg" alt="日本語"></a>
</p>

# ClaudeSessionsViewer

ClaudeSessionsViewer is a local viewer for searching and browsing Claude chat history.
It scans the following two storage locations:

- `~/.claude/projects/` (Claude Code CLI / JSONL)
- `%APPDATA%\Claude\IndexedDB\` (Claude Desktop / LevelDB)

![image](/image/00001.jpg)

## Prerequisites

- Python 3 (`py -3`, `python`, or `python3` command available)
- A web browser (Edge / Chrome, etc.)

If Python 3 is not installed (Windows / winget):

```powershell
winget install -e --id Python.Python.3.12
```

Verify installation:

```powershell
py -3 --version
```

## Launch on Windows

- `scripts\windows\launch_viewer.bat`
- `scripts\windows\stop_viewer.bat`

`launch_viewer.bat` starts `viewer.py` on Windows and opens your browser.

Default URL:

```text
http://127.0.0.1:8767
```

## Run Directly (Python)

```powershell
python viewer.py
```

Or:

```bash
python3 viewer.py
```

## Default Scan Paths

- Claude Code CLI
  - `~/.claude/projects`
  - `%USERPROFILE%\.claude\projects`
  - `WIN_HOME\.claude\projects` (when `WIN_HOME` is set)
  - `/mnt/c/Users/*/.claude/projects`
  - `\\wsl.localhost\<distro>\home\*\.claude\projects` (auto-detected when launched on Windows)
- Claude Desktop
  - `%APPDATA%\Claude\IndexedDB`
  - `%USERPROFILE%\AppData\Roaming\Claude\IndexedDB`
  - `WIN_HOME\AppData\Roaming\Claude\IndexedDB` (when `WIN_HOME` is set)
  - `/mnt/c/Users/*/AppData/Roaming/Claude/IndexedDB`

To use a custom Claude Code CLI directory:

```powershell
$env:CLAUDE_SESSIONS_DIR = 'C:\path\to\.claude\projects'
python viewer.py
```

Notes:

- You can also override with `SESSIONS_DIR`.
- Multiple paths are separated by `os.pathsep` (`;` on Windows, `:` on Unix/WSL).
- On Windows, `viewer.py` also runs `wsl.exe -l -q` and scans each distro's `~/.claude/projects`.
- Set `CLAUDE_WSL_DISTROS` to limit which distros are scanned (example: `Ubuntu;Debian`).

## UI Features

- Left pane: session list (newest first)
- Session `source` labels (`CLI(JSONL)` / `Desktop(LevelDB)`) are shown in the list
- Top-left filters: narrow down by `project/path`, date range, keyword, and `source`
- Search is partial match against `project`, `relative_path`, and first user input
- `project/path` matching checks both `project` and `relative_path`, and treats `-`, `/`, and `\` as equivalent separators
- `project/path` / date range / keyword / `source` are always combined with AND
- `AND/OR` switch applies only within the keyword field
  - `AND`: must include all space-separated keywords
  - `OR`: must include at least one space-separated keyword
- Right pane: timeline of events for the selected session
  - The detail header also shows the `source` label (`Claude Code CLI` / `Claude Desktop`)
  - Display options
    - `Show only user instructions`
    - `Show only AI responses`
    - `Reverse display order`
  - A `Copy Resume Command` button copies `claude --resume <session_id>`
  - Shows `message` (`user` / `assistant`) and `function_call` / `function_output` / `agent_update`
  - `user` messages use a light-blue background, `assistant` uses light-green, and execution context such as `AGENTS.md` / `environment_context` is shown in gray

## Important Limitations

- Claude Code CLI (JSONL) can be parsed and displayed structurally.
- Claude Desktop (IndexedDB/LevelDB) has the following characteristics and limitations.

### Claude Desktop Data Structure

Investigation has revealed that Claude Desktop only stores **unsent chat draft messages** in `%APPDATA%\Claude\IndexedDB\`.

| Item | Description |
|------|-------------|
| What is stored | Chat input drafts (unsent messages) |
| What is NOT stored | Sent conversation history (user messages and AI responses) |
| Where sent conversations are stored | Anthropic's servers (not available locally) |

This viewer properly parses the LevelDB log files (`.log`) and accurately displays draft messages.

- Decodes LevelDB WriteBatch records block by block
- Recovers Chromium IndexedDB string keys (UTF-16BE)
- Skips the Blink SerializedScriptValue header and extracts UTF-16LE JSON
- Collects plain text from TipTap / ProseMirror document trees

> **Why conversation history is not visible**
> Claude Desktop is Electron wrapping the claude.ai web application. Conversation history is stored in Anthropic's cloud and is not synchronized to local IndexedDB. Therefore, this viewer can only display drafts currently being typed.

## Environment Variables

- `HOST`: Bind address (default: `127.0.0.1`)
- `PORT`: Port (default: `8767`)
- `CLAUDE_SESSIONS_DIR` / `SESSIONS_DIR`: Override Claude Code CLI JSONL root path(s)

## ❗This project is licensed under the MIT License, see the LICENSE file for details
