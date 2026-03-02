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

## Main Features

- Keyword search in session list (AND/OR)
- `project/path` filter (partial match)
- Date range filter
- Source type filter (Claude Code CLI / Claude Desktop)
- Session detail view (show only user messages, reverse order)

`project/path` search behavior:

- Searches both `project` and `relative_path`.
- Treats `-`, `/`, and `\` as equivalent separators.
- Example: `C:\junichi\takeda\source` / `C:/junichi/takeda/source` / `C--junichi-takeda-source`

Display behavior:

- `project` values are shown in Windows path style when possible (`C:\...`).
- Slug-style values such as `C--foo-bar...` are normalized to `C:\foo\bar\...`.

## Important Limitations

- Claude Code CLI (JSONL) can be parsed and displayed structurally.
- Claude Desktop (IndexedDB/LevelDB) is binary, so it is currently shown via text/JSON snippet extraction.
  - Scans both UTF-8 and UTF-16LE.
  - Extracts nested JSON using balanced-brace parsing.
  - This is not a full-fidelity history reconstruction.
  - A dedicated decoder may be added in the future.

## Environment Variables

- `HOST`: Bind address (default: `127.0.0.1`)
- `PORT`: Port (default: `8767`)
- `CLAUDE_SESSIONS_DIR` / `SESSIONS_DIR`: Override Claude Code CLI JSONL root path(s)

## ❗This project is licensed under the MIT License, see the LICENSE file for details
