<p align="left">
  <a href="README_en.md"><img src="https://img.shields.io/badge/English Mode-blue.svg" alt="English"></a>
  <a href="README.md"><img src="https://img.shields.io/badge/日本語 モード-red.svg" alt="日本語"></a>
</p>

# ClaudeSessionsViewer

A local viewer for searching and browsing Claude chat data.  
It scans the following two storage families:

- `~/.claude/projects/` (Claude Code CLI / JSONL)
- `%APPDATA%\Claude\IndexedDB\` (Claude Desktop / LevelDB)

## Screen Layout

### Main Screen

![image](/image/00001.jpg)

### Label Manager Screen

![image](/image/00002.jpg)

## Directory Layout

```text
.
├─ viewer.py
└─ scripts
   └─ windows
      ├─ launch_viewer.bat
      └─ stop_viewer.bat
```

## Prerequisites

- Python 3 (`py -3`, `python`, or `python3` must be available)
- A web browser (Edge / Chrome, etc.)

If Python 3 is not installed (Windows / winget):

```powershell
winget install -e --id Python.Python.3.12
```

Verify installation:

```powershell
py -3 --version
```

## Launch

### One-click Launch on Windows

- `scripts\windows\launch_viewer.bat`
- `scripts\windows\stop_viewer.bat`

`launch_viewer.bat` starts `viewer.py` on Windows and opens the browser.

Open the following URL after launch:

```text
http://127.0.0.1:8767
```

### Launch Directly with Python

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

## Options

To use custom Claude Code CLI directories, set `CLAUDE_SESSIONS_DIR`.  
You can also override with `SESSIONS_DIR`. Multiple roots are separated with `os.pathsep` (`;` on Windows, `:` on Unix/WSL).

```powershell
$env:CLAUDE_SESSIONS_DIR = 'C:\path\to\.claude\projects'
python viewer.py
```

To change the bind address, set `HOST`.

```powershell
$env:HOST = '0.0.0.0'
python viewer.py
```

## UI Features

- Left pane: session list in reverse chronological order
  - Shows session `source` labels (`CLI(JSONL)` / `Desktop(LevelDB)`) and session labels in the list
  - `Reload` reloads the session list
  - `Clear` resets the search conditions in the left pane
  - `Hide` / `Show` collapses or expands the filter area
- Top-left filters
  - Filter by `project/path`, date range, keyword, `source`, session label, and event label
  - Keyword search uses a SQLite-backed search index
  - `project/path` matches both `project` and `relative_path`, and treats `-`, `/`, and `\` as equivalent separators
  - Search targets include `message`, `function_call.arguments`, `function_output.output`, and `agent_update.message`
  - `project/path`, date range, `source`, and label filters are always evaluated with AND
  - `AND/OR` only affects the keyword field
    - `AND`: all space-separated keywords must match
    - `OR`: any space-separated keyword may match
- Right pane: event timeline for the selected session
  - The detail header shows the `source` label (`Claude Code CLI` / `Claude Desktop`)
  - Display options:
    - `Show only user instructions`
    - `Show only AI responses`
    - `Reverse display order`
    - `event label: all` filter
  - `Refresh` reloads only the selected session
  - `Copy Resume Command` copies `claude --resume <session_id>`
  - Shows session labels and `Add Session Label`
  - Shows, adds, and removes labels for each event
  - Displays `message` (`user` / `assistant`)
  - Displays `function_call`, `function_output`, and `agent_update`
  - `user` messages use a light blue background, `assistant` uses light green, and execution context such as `AGENTS.md` / `environment_context` is shown in gray
- Label manager
  - Opens in a separate window from the `Label Manager` button in the top-right corner
  - Manages session labels and event labels in one place
  - Label colors can be entered directly as `#hex`, `rgb(...)`, or `oklch(...)`, or selected from color presets

## Important Limitations

- Claude Code CLI (JSONL) can be parsed and displayed structurally.
- Claude Desktop (IndexedDB/LevelDB) has the following behavior and limitations.

### Claude Desktop Data Structure

Investigation showed that Claude Desktop stores only **unsent chat draft messages** in `%APPDATA%\Claude\IndexedDB\`.

| Item | Description |
| ---- | ---- |
| Stored locally | Draft text in the chat input box (unsent messages) |
| Not stored locally | Sent conversation history (user messages and AI responses) |
| Actual storage for sent conversations | Anthropic servers, not local files |

This viewer properly parses LevelDB log files (`.log`) and reconstructs draft messages.

- Decodes LevelDB WriteBatch records block by block
- Restores Chromium IndexedDB string keys (UTF-16BE)
- Skips the Blink SerializedScriptValue header and extracts UTF-16LE JSON
- Collects plain text from TipTap / ProseMirror document trees

> **Why sent conversations are not visible**  
> Claude Desktop is an Electron wrapper around the claude.ai web app. Conversation history is stored in Anthropic's cloud and is not synchronized to local IndexedDB. Because of that, this viewer can only show drafts currently being edited.

## Notes

- The search index is stored in `.cache/search_index.sqlite3` and only changed sessions are re-indexed.
- On Windows, `viewer.py` runs `wsl.exe -l -q` and also scans each distro's `~/.claude/projects`.
- To limit which distros are auto-detected, set `CLAUDE_WSL_DISTROS` (for example: `Ubuntu;Debian`).
- To keep the UI responsive with large logs, the list is capped at `400` sessions and the detail view is capped at `4000` events.
- By default, the viewer listens only on `127.0.0.1` for local use.

## License

This project is provided under the MIT License. See the `LICENSE` file for details.
