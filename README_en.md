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

- Header
  - Includes language / currency switches plus `Label Manager`, `Cost Summary`, `Meta`, and `Shortcuts`
  - In vertical layout, `Hide List` / `Show List` toggles the entire left pane
  - Shows today's usage summary beneath the header actions
- Left pane
  - Two tabs: `Session List` and `Label List`
  - Session cards show the `source` label (`Claude Code CLI` / `Claude Desktop`) and any session labels
  - Sort order can be switched between newest, oldest, and last-updated
  - Shows a loading state on first load and an updating overlay during manual `Reload`
  - `Reload` refreshes the list, `Clear` resets the filters, and `Hide` / `Show` collapses the filter area
  - The label list lets you open sessions and events grouped by label
- Left-pane search / filters
  - Filter by `Working directory`, `Keyword`, `Condition (AND / OR)`, `Start date`, `End date`, `Event start datetime`, `Event end datetime`, `source`, `subagents`, `Session label`, and `Event label`
  - `Start date` / `End date` use date inputs; event datetimes use split `date + time` inputs
  - The time field for an event datetime becomes enabled after the corresponding date is entered
  - Keyword search is backed by a SQLite index
  - Working directory, datetime, `source`, `subagents`, and label filters are always combined with AND
  - The `AND / OR` switch applies only to the keyword field
    - `AND`: all space-separated keywords must match
    - `OR`: at least one space-separated keyword must match
- Right pane
  - Shows the event timeline for the selected session
  - Displays a loading state on first load and an updating overlay during manual `Refresh`
  - Shows the `source` label in the detail header
  - The top area contains display filters, `Refresh`, `Clear`, and buttons to expand detail actions
  - Display options
    - "Show only user instructions"
    - "Show only AI responses"
    - "Show only each input and final response"
      - Keeps one `user` message per turn and only the last `assistant` message before the next `user`
    - "Reverse display order"
    - "Show only token usage"
    - `Event label` filter
    - `Cost sort` (total tokens / cost / score)
  - Detail keyword search
    - `Filter`: shows only events containing the keyword
    - `Search`: highlights matches and moves through them with `Previous` / `Next`
    - `Clear Search`: clears the input, filter, and search state together
    - Matching is a plain substring match, not AND / OR logic
  - `Event start datetime` / `Event end datetime` can narrow the right-pane timeline
  - `Clear Date/Time` resets the detail datetime filters
  - "Copy Resume Command" copies `claude --resume <session_id>`
  - "Copy Displayed Messages" copies all currently visible messages
  - Shows session labels and supports "Add Session Label"
  - Supports per-event label display / add / remove
  - Each `message` event has its own `Copy` button
  - "Selection Mode" lets you select individual `message` events and copy them with `Copy Selected`
    - Selected events are preserved even when filters change
    - "Show selected events only" narrows the view to the current selection
  - "Anchor Selection Mode" lets you pick a single `message` and show only messages before or after it
  - Displays `message` (`user` / `assistant`), `tool_result`, `queue`, `progress`, `notice`, `snippet`, and other event types
  - `user` messages are light blue, `assistant` light green, system / notice events gray, and tool events teal
- Label Manager
  - Opens in a separate window from the `Label Manager` button
  - Manages session labels and event labels in one shared UI
  - Label colors can be entered as `#hex`, `rgb(...)`, or `oklch(...)`, or chosen from presets
- Cost Summary
  - Opens in a separate page for monthly, weekly, and daily usage summaries
  - Lets you compare session-based totals with `token usage` event-based totals

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
| `5`         | Toggle "Show only token usage"                                      |
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
