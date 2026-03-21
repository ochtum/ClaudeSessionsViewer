<p align="left">
  <a href="README_en.md"><img src="https://img.shields.io/badge/English Mode-blue.svg" alt="English"></a>
  <a href="README.md"><img src="https://img.shields.io/badge/日本語 モード-red.svg" alt="日本語"></a>
</p>

# ClaudeSessionsViewer

A local viewer that lets you browse, inspect, and search your Claude Code session history. You can attach labels to sessions and events you want to remember, and search for them later.

- This tool supports Japanese / English / Simplified Chinese / Traditional Chinese.
- Feedback and feature requests are welcome — feel free to open an issue.

## Screenshots

### Main Screen

![image](/image/00001.jpg)

### Label Manager

![image](/image/00002.jpg)

### Keyboard Shortcuts List

![image](/image/00003.jpg)

⭐ If you find this project useful, a Star would be appreciated!

👀 Want to stay updated? Hit Watch!

## Getting Started

Download the `app-framework-dependent` folder from [Releases](../../releases), extract it, and run `CodexSessionsViewer.exe`.

> **Note:** This tool requires the .NET 10 SDK or .NET 10 Runtime. If you are unsure whether it is installed or prefer not to install it, download the `app-self-contained` folder instead.

---

To build from source, run one of the following PowerShell scripts:

- Framework-dependent build (requires .NET 10 SDK or .NET 10 Runtime already installed)

```
.\publish.ps1 -CleanOutput
```

- Self-contained build (no .NET installation required on the target machine)

```
.\publish.ps1 -SelfContained -CleanOutput
```

## Default Scan Paths

- Claude Code CLI
  - `~/.claude/projects`
  - `%USERPROFILE%\.claude\projects`
  - `WIN_HOME\.claude\projects` (when `WIN_HOME` is set)
  - `/mnt/c/Users/*/.claude/projects`
  - `\\wsl.localhost\<distro>\home\*\.claude\projects` (WSL distros are auto-detected on Windows)
- Claude Desktop
  - `%APPDATA%\Claude\IndexedDB`
  - `%USERPROFILE%\AppData\Roaming\Claude\IndexedDB`
  - `WIN_HOME\AppData\Roaming\Claude\IndexedDB` (when `WIN_HOME` is set)
  - `/mnt/c/Users/*/AppData/Roaming/Claude/IndexedDB`

## UI Features

- Left pane: session list (newest first)
  - Each session shows a `source` label (`Claude Code CLI` / `Claude Desktop`) and any session labels
  - A loading indicator appears during the initial load
  - `Reload` refreshes the session list
    - A manual `Reload` shows an updating overlay and button state feedback
  - `Clear` resets all left-pane search conditions
  - `Hide` / `Show` collapses or expands the search filter area
  - In vertical layout, the `Hide List` / `Show List` button in the upper-right header toggles the entire left pane
- Top-left filters
  - Filter by `project/path` / `Start date` / `End date` / `Event start datetime` / `Event end datetime` / keyword / `source` / session label / event label
  - `Start date` / `End date` use the browser's native `date` input; event datetimes use split `date + time` inputs
  - The time field for an event datetime becomes enabled after the corresponding date is entered
  - Keyword search uses full-text search backed by a SQLite index
  - `project/path` matches both `project` and `relative_path`, treating `-`, `/`, and `\` as equivalent separators
  - Search targets include text extracted from `message`, `tool_result`, `queue`, `progress`, `notice`, `snippet`, and other event body content
  - `project/path`, datetime, `source`, and label conditions are always combined with AND
  - The `AND` / `OR` toggle applies only to the keyword field
    - `AND`: all space-separated keywords must be present
    - `OR`: at least one space-separated keyword must be present
- Right pane: chronological event timeline for the selected session
  - A loading indicator appears during the first detail load; a manual `Refresh` shows an updating overlay
  - The detail header shows the `source` label (`Claude Code CLI` / `Claude Desktop`)
  - The detail header has four rows
    - Row 1: display filters, `Clear`, `Refresh`, and a `Hide` / `Show` button that collapses rows 2–4 together
    - Row 2: copy, label, and selection-copy action buttons
    - Row 3: keyword input, `Filter`, `Search`, `Previous`, `Next`, `Keyword Clear`
    - Row 4: single-message anchor selection mode, clear-anchor action, and before/after message display
  - Display options
    - "Show only user instructions"
    - "Show only AI responses"
    - "Show only each input and final response"
      - Keeps one `user` message per turn and only the last `assistant` message before the next `user`
    - "Reverse display order"
    - `event label: all` filter
  - Keyword search
    - `Filter`: shows only events containing the keyword
    - `Search`: highlights matches and navigates between them with `Previous` / `Next`
    - `Keyword Clear`: clears the input, filter, and search state all at once
    - Matching is a plain substring match (not AND / OR)
    - The search covers the full body text of displayed events
  - `Event start datetime` / `Event end datetime` narrow the event timeline shown in the right pane
  - Right-pane event datetime filters also use split `date + time` inputs; the time field is enabled after a date is entered
  - `Clear` resets the detail-side display filters
  - `Refresh` reloads only the currently selected session
  - "Copy Resume Command" copies `claude --resume <session_id>` to the clipboard
  - "Copy Displayed Messages" copies all currently visible messages (reflecting active filters)
  - Session label display and "Add Session Label"
  - Per-event label display / add / remove
  - Each `message` event has its own "Copy" button
  - "Selection Mode" lets you check individual `message` events and copy them together with "Copy Selected"
    - Already-selected messages are preserved even when filters change
  - "Anchor Selection Mode" lets you pick a single `message` as an anchor, then show only messages before or after that anchor
  - Displays `message` (`user` / `assistant`), `tool_result`, `queue`, `progress`, `notice`, `snippet`, and other event types
  - `user` messages have a light blue background, `assistant` light green, system / notice events gray, and tool events teal
- Label Manager
  - Opens in a separate window via the "Label Manager" button in the upper-right corner
  - Manages session labels and event labels in a single shared UI
  - Label colors can be entered directly as `#hex`, `rgb(...)`, or `oklch(...)`, or picked from color presets

## Keyboard Shortcuts

Shortcuts are disabled while an input field is focused. Press `Esc` to close the shortcut list or label picker, or to move focus out of a search field.

| Key         | Action                                                              |
| ----------- | ------------------------------------------------------------------- |
| `F5`        | Refresh the current list or session detail                          |
| `Shift + F` | Toggle the left-pane filter area                                    |
| `Shift + L` | Clear the left pane                                                 |
| `/`         | Focus the search input field                                        |
| `N`         | Jump to the next search match in the detail view                    |
| `P`         | Jump to the previous search match in the detail view                |
| `M`         | Toggle the `path / cwd / time` metadata display                    |
| `[`         | Open the previous session                                           |
| `]`         | Open the next session                                               |
| `1`         | Toggle "Show only user instructions"                                |
| `2`         | Toggle "Show only AI responses"                                     |
| `3`         | Toggle "Show only each input and final response"                    |
| `4`         | Toggle "Reverse display order"                                      |
| `Shift + D` | Clear right-pane filters and active modes                           |
| `Shift + T` | Toggle detail action rows                                           |
| `Shift + R` | Copy the resume command (`claude --resume <session_id>`)            |
| `Shift + C` | Copy displayed messages                                             |
| `Shift + S` | Toggle selection mode                                               |
| `Shift + X` | Copy selected messages                                              |
| `Shift + G` | Toggle anchor selection mode                                        |
| `Shift + H` | Clear the anchor                                                    |
| `,`         | Show only messages before the anchor                                |
| `.`         | Show only messages after the anchor                                 |
| `Esc`       | Close the shortcut list or label picker; unfocus search input field |

## Important Limitations

- Claude Code CLI (JSONL) sessions are fully parsed and displayed in a structured format.
- Claude Desktop (IndexedDB / LevelDB) has the following behavior and limitations.

### Claude Desktop Data Structure

Investigation has revealed that Claude Desktop stores only **unsent chat draft messages** in `%APPDATA%\Claude\IndexedDB\`.

| Item                                | Description                                            |
| ----------------------------------- | ------------------------------------------------------ |
| Stored locally                      | Draft text in the chat input box (unsent messages)     |
| Not stored locally                  | Sent conversation history (user messages & AI replies) |
| Where sent conversations are stored | Anthropic's servers (not available locally)             |

This viewer properly parses LevelDB log files (`.log`) and accurately displays draft messages by:

- Decoding LevelDB WriteBatch records block by block
- Restoring Chromium IndexedDB string keys (UTF-16BE)
- Skipping the Blink SerializedScriptValue header and extracting UTF-16LE JSON
- Collecting plain text from TipTap / ProseMirror document trees

> **Why sent conversations are not visible**
> Claude Desktop is an Electron wrapper around the claude.ai web app. Conversation history is stored in Anthropic's cloud and is not synced to local IndexedDB. As a result, this viewer can only display drafts currently being edited.

## Notes

- Label data is stored in `.cache/label-store-claude.json`.
- On Windows, `wsl.exe -l -q` is used to enumerate WSL distros, and each distro's `~/.claude/projects` is scanned automatically.
- To limit which distros are auto-detected, set the `CLAUDE_WSL_DISTROS` environment variable (e.g., `Ubuntu;Debian`).
- To keep the UI responsive with large logs, the session list is capped at `400` entries and the detail view at `4,000` events.
- By default, the viewer listens only on `127.0.0.1` (localhost).

## File Structure

```text
.
├── .gitignore                           # Root exclusion rules
├── LICENSE                              # License
├── README.md                            # Japanese README
├── README_en.md                         # English README
├── publish.ps1                          # Publish script for distribution
├── image/
│   ├── 00001.jpg                        # Main screen screenshot for README
│   ├── 00002.jpg                        # Label Manager screenshot for README
│   └── 00003.jpg                        # Shortcuts list screenshot for README
└── src/
    ├── .cache/
    │   └── label-store-claude.json      # Label definitions and associations
    ├── ClaudeSessionsViewer.sln         # Solution file
    ├── ClaudeSessionsViewer.csproj      # ASP.NET Core / Blazor project definition
    ├── Program.cs                       # App startup, URL configuration, API endpoints
    ├── appsettings.json                 # Production settings
    ├── appsettings.Development.json     # Development settings
    ├── Components/
    │   ├── App.razor                    # HTML root and shared script imports
    │   ├── Routes.razor                 # Routing definition
    │   ├── _Imports.razor               # Shared Razor using directives
    │   ├── Layout/
    │   │   ├── MainLayout.razor         # Shared layout
    │   │   ├── MainLayout.razor.css     # Shared layout styles
    │   │   ├── ReconnectModal.razor     # Reconnect modal UI
    │   │   ├── ReconnectModal.razor.css # Reconnect modal styles
    │   │   └── ReconnectModal.razor.js  # Reconnect modal script
    │   └── Pages/
    │       ├── Error.razor              # Error page
    │       ├── Home.razor               # Main page
    │       ├── Labels.razor             # Label Manager page
    │       └── NotFound.razor           # 404 page
    ├── Models/
    │   └── ViewerDtos.cs                # DTOs for API requests / responses
    ├── Properties/
    │   ├── AssemblyInfo.cs              # Version information
    │   └── launchSettings.json          # Local development launch settings
    ├── Services/
    │   ├── LabelStore.cs                # Label persistence and validation logic
    │   └── ViewerService.cs             # Session discovery, loading, and search logic
    └── wwwroot/
        ├── app.css                      # Global styles
        ├── css/
        │   ├── labels.css               # Label Manager styles
        │   └── viewer.css               # Main page styles
        ├── icons/
        │   ├── claude-sessions-viewer.svg # App icon (Claude edition)
        │   └── codex-sessions-viewer.svg  # App icon (Codex edition)
        └── js/
            ├── labels.js                # Label Manager script
            └── viewer.js                # Main page script
```

## ❗ This project is provided under the MIT License. See the LICENSE file for details.
