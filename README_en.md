<p align="left">
  <a href="README_en.md"><img src="https://img.shields.io/badge/English Mode-blue.svg" alt="English"></a>
  <a href="README.md"><img src="https://img.shields.io/badge/日本語 モード-red.svg" alt="日本語"></a>
</p>

# ClaudeSessionsViewer

This is a local viewer that lets you list, inspect in detail, and search Claude Code history. You can also attach labels to content you want to remember and search for it later.

- This tool supports Japanese / English / Simplified Chinese / Traditional Chinese.
- Please feel free to open an issue with any feedback or feature requests.
- The first launch is slower, but after the initial loading process completes it runs quickly thanks to caching.
  - We plan to improve startup speed soon by adding deferred loading.

## Screen Layout

### Main Screen

![image](/image/00001.jpg)

### Label Management Screen

![image](/image/00002.jpg)

### Shortcut Key List Screen

![image](/image/00003.jpg)

⭐ If this project is useful to you, I would appreciate a Star!

👀 If you want to follow updates, please consider using Watch too!

## How to Run

After downloading the `app-framework-dependent` folder from Releases, extract it and run the included `run.cmd`.

Note: Running this tool requires the .NET 10 SDK or .NET 10 Runtime. If you are not sure whether it is installed, or if you do not want to install it, download the `app-self-contained` folder instead.

---

If you build from `src`, run one of the following PowerShell scripts.

- Framework-dependent build (if the .NET 10 SDK or .NET 10 Runtime is already installed)

```powershell
.\publish.ps1 -CleanOutput
```

- Self-contained build (if the .NET 10 SDK or .NET 10 Runtime installation status is unknown, or if you do not want to install it)

```powershell
.\publish.ps1 -SelfContained -CleanOutput
```

## Default Scan Paths

- Claude Code CLI
  - `~/.claude/projects`
  - `%USERPROFILE%\.claude\projects`
  - `WIN_HOME\.claude\projects` (when `WIN_HOME` is specified)
  - `/mnt/c/Users/*/.claude/projects`
  - `\\wsl.localhost\<distro>\home\*\.claude\projects` (WSL distros are automatically detected when launched on Windows)
- Claude Desktop
  - `%APPDATA%\Claude\IndexedDB`
  - `%USERPROFILE%\AppData\Roaming\Claude\IndexedDB`
  - `WIN_HOME\AppData\Roaming\Claude\IndexedDB` (when `WIN_HOME` is specified)
  - `/mnt/c/Users/*/AppData/Roaming/Claude/IndexedDB`

## Screen Features

- Header
  - Language switch (`日本語` / `English` / `简体中文` / `繁體中文`) and currency switch (`USD` / `JPY` / `CNY` / `TWD` / `HKD`) are placed in the upper right
  - Includes `Label Management`, `Cost Display`, `Meta Display`, `Shortcuts`, and the list visibility toggle for mobile layouts
  - Displays a "Today's usage" summary below the header so you can quickly check tokens, cost, and score
  - `Meta Display` is hidden by default. It lets you inspect the selected session's `session root` / `path` / `cwd` / `time` / `source` / `events` / `raw lines`
- Left pane
  - Two tabs: `Session List` and `Label List`
  - The session list shows the `source` label (`Claude Code CLI` / `Claude Desktop`) and session labels
  - Displays the count at the top as `sessions: filtered/total`
  - Sort order can be switched with the `Newest` / `Oldest` / `Last Updated` tabs
  - `Clear` resets the search and filter conditions in the left pane
  - `Show Filters` / `Hide Filters` collapses the search and filter area
  - In vertical layout, you can toggle the entire left pane with `Hide List` / `Show List` in the upper right of the header
- Left-pane search and filters
  - Filter by `cwd` / keyword / `Start Date` / `End Date` / `Event Start DateTime` / `Event End DateTime` / `source` / `subagents` / session label / event label
  - Keyword search covers not only `message`, but also `function_call.arguments` / `function_output.output` / `agent_update.message`
  - In the keyword field, text enclosed in double quotes is treated as a single phrase
    - Example: search `"Working Space"` as one phrase
  - `cwd` / date and time / `source` / label conditions are always combined with AND
  - The `AND/OR` switch applies only to the keyword field
    - `AND`: must include all space-separated keywords
    - `OR`: must include any of the space-separated keywords
  - The time field for event date/time becomes enabled when the corresponding date is entered
  - Filter conditions are preserved the next time the app is launched
- Left-pane label list
  - Displays labeled sessions and labeled events grouped by label
  - Distinguishes item types such as `message` / `function_call` / `function_output` / `agent_update` / `token_usage`
  - Clicking an item jumps to the target session or target event
- Right pane: timeline view of events in the selected session
  - Shows a loading indicator on the first detail load, and an overlay while details are being updated during manual `Refresh`
  - The detail toolbar is organized into `Display` / `Actions` / `Search` / `Range Selection`
  - `Detail Actions` / `Search` / `Range Selection` can each be opened or closed independently
  - If no session is selected, display, search, and range-selection operations are disabled
- Right-pane display and actions
  - Display conditions: `Show user instructions only` / `Show AI responses only` / `Show only each input and the final response` / `Reverse display order` / `Show token usage only` / label filter
  - `Cost Sort` can reorder user-message-based groups by `Total Tokens` / `Cost` / `Score`
  - `Refresh` re-fetches only the selected session
  - `Clear` resets the entire state of the right pane
    - Display filters
    - Detail keyword input, `Filter` / `Search` state
    - Selection mode, selected events
    - Anchor selection mode, anchor, show before anchor / show after anchor
    - Open label picker
  - `Copy Session Resume Command` copies `codex resume <session ID>`
  - `Copy Displayed Messages` copies all currently displayed `message` items together
  - Shows session labels and `Add Label to Session`
  - Supports label display / add / remove for each event
  - Each `message` event has its own `Copy` button
- Right-pane search and selection
  - Detail keywords separate `Filter` and `Search`
    - `Filter`: shows only events that contain the keyword
    - `Search`: highlights matches and moves through them with `Previous` / `Next`
    - Hit count is shown as `current / total`
    - `Clear Search`: clears the input field, filter, and search state together
  - Detail keywords use plain partial matching of the entered text, not AND / OR logic
  - Search targets are `message` / `function_call` / `function_output` / `agent_update`
  - Pressing `Enter` in the search field runs the search and removes focus, so you can move with `N` / `P`
  - `Event Start DateTime` / `Event End DateTime` can narrow the event timeline shown in the right pane
  - The event date/time filter in the right pane also uses split `date + time` inputs, and the time field is enabled after entering a date
  - In `Selection Mode`, you can check events one by one and copy them together with `Copy Selected`
    - Even while a filter is active, already selected events are preserved
  - `Show Selected Events Only` narrows the view to selected events only
  - In `Anchor Selection Mode`, you can choose a single `message` and filter with `Show After Anchor Only` / `Show Before Anchor Only`
- Event display
  - `message` (`user` / `assistant` / `developer`)
  - `user` uses a light blue background, while execution context such as `AGENTS.md` and `environment_context` uses a gray background
  - `function_call` / `function_output`
  - `agent_update`
  - `token_usage`
- Label Management
  - Opens in a separate window from the `Label Management` button in the upper right
  - Shares the same language setting as the main screen
  - Manages session labels and event labels together
  - Label colors can be entered directly as `#hex` / `rgb(...)` / `oklch(...)`, or selected from color presets
  - In the label-addition UI as well, you can view candidates while keeping their colors visible
- Cost Display
  - Opens in a separate window from the `Cost Display` button in the upper right
  - Lets you review usage totals while switching the cost display according to the selected currency

## Shortcut Keys

While the cursor is inside an input field, shortcuts are not executed. Press `Esc` to close the shortcut list or label picker, or to move focus away from a search input.

| Key         | Action                                                                            |
| ----------- | --------------------------------------------------------------------------------- |
| `F5`        | Refresh the currently displayed list or session details                           |
| `Shift + F` | Toggle the filter display in the left pane                                        |
| `Shift + L` | Run `Clear` in the left pane                                                      |
| `/`         | Focus the search input field                                                      |
| `N`         | Move to the next hit in detail search                                             |
| `P`         | Move to the previous hit in detail search                                         |
| `M`         | Toggle meta display for `path / cwd / time`                                       |
| `[`         | Open the previous session                                                         |
| `]`         | Open the next session                                                             |
| `1`         | Toggle `Show user instructions only`                                              |
| `2`         | Toggle `Show AI responses only`                                                   |
| `3`         | Toggle `Show only each input and the final response`                              |
| `4`         | Toggle `Reverse display order`                                                    |
| `5`         | Toggle `Show token usage only`                                                    |
| `Shift + D` | Run `Clear` in the right pane                                                     |
| `Shift + T` | Toggle showing and hiding detail actions                                          |
| `Shift + R` | Copy the session resume command (`claude --resume <session ID>`)                  |
| `Shift + C` | Copy the displayed messages                                                       |
| `Shift + S` | Toggle the start and end of selection mode                                        |
| `Shift + X` | Copy the selected messages                                                        |
| `Shift + G` | Toggle the start and end of anchor selection mode                                 |
| `Shift + H` | Clear the anchor                                                                  |
| `,`         | Show only items before the anchor                                                 |
| `.`         | Show only items after the anchor                                                  |
| `Esc`       | Close the shortcut list or add-label popup, and move focus away from search input |

## Important Constraints

- Claude Code CLI (JSONL) can be displayed in a structured format.
- Claude Desktop (IndexedDB/LevelDB) has the following specifications and limitations.

### About Claude Desktop's Data Structure

As a result of investigation, it was found that Claude Desktop stores only **chat draft messages before sending** in `%APPDATA%\Claude\IndexedDB\`.

| Item                               | Description                                             |
| ---------------------------------- | ------------------------------------------------------- |
| Data that is stored                | Drafts in the chat input field (unsent messages)        |
| Data that is not stored            | Sent conversation history (user messages and AI replies) |
| Where sent conversations are stored | Anthropic's servers (not available locally)            |

This Viewer properly parses LevelDB log files (`.log`) and accurately displays draft messages.

- Decode LevelDB WriteBatch records block by block
- Restore Chromium IndexedDB string keys (UTF-16BE)
- Skip the Blink SerializedScriptValue header and extract UTF-16LE JSON
- Collect plain text from TipTap / ProseMirror document trees

> **Why conversation history is not visible**  
> Claude Desktop is an Electron wrapper around the claude.ai web app. Conversation history is stored in Anthropic's cloud and is not synchronized to local IndexedDB. Because of this, this Viewer can display only the draft currently being edited.

## Notes

- Label data is stored in `.cache/label-store-claude.json`.
- If no actual data from Claude Code / Claude Desktop is found at all, the bundled `sample-data/claude/projects` is automatically displayed as dummy data.
- If you want to explicitly change the source location, you can specify the Claude Code CLI root with `CLAUDE_SESSIONS_DIR` or `SESSIONS_DIR`.
- On Windows, `wsl.exe -l -q` is used to enumerate WSL distros, and `~/.claude/projects` in each distro is also discovered automatically.
- If you want to limit which distros are auto-detected, specify `CLAUDE_WSL_DISTROS` (example: `Ubuntu;Debian`).
- To handle large logs, the list is limited to a maximum of `400` items and events are limited to a maximum of `4000`.
- By default, the Viewer listens only on localhost (`127.0.0.1`).

## File Structure

```text
.
├── .gitignore                           # Root exclusion settings
├── LICENSE                              # License
├── README.md                            # Japanese README
├── README_en.md                         # English README
├── publish.ps1                          # Publish script for distribution
├── image/
│   ├── 00001.jpg                        # Main screen sample for README
│   ├── 00002.jpg                        # Label management screen sample for README
│   └── 00003.jpg                        # Shortcut screen sample for README
└── src/
    ├── .cache/
    │   └── label-store-claude.json      # Storage location for label definitions and mappings
    ├── ClaudeSessionsViewer.sln         # Solution
    ├── ClaudeSessionsViewer.csproj      # ASP.NET Core / Blazor project definition
    ├── Program.cs                       # App startup, URL settings, API endpoint definitions
    ├── appsettings.json                 # Production settings
    ├── appsettings.Development.json     # Development settings
    ├── Components/
    │   ├── App.razor                    # HTML root and shared script loading
    │   ├── Routes.razor                 # Routing definitions
    │   ├── _Imports.razor               # Shared Razor using directives
    │   ├── Layout/
    │   │   ├── MainLayout.razor         # Shared layout
    │   │   ├── MainLayout.razor.css     # Styles for the shared layout
    │   │   ├── ReconnectModal.razor     # Reconnect modal UI
    │   │   ├── ReconnectModal.razor.css # Styles for the reconnect modal
    │   │   └── ReconnectModal.razor.js  # Script for the reconnect modal
    │   └── Pages/
    │       ├── Error.razor              # Error screen
    │       ├── Home.razor               # Main screen
    │       ├── Labels.razor             # Label management screen
    │       └── NotFound.razor           # 404 screen
    ├── Models/
    │   └── ViewerDtos.cs                # DTOs for API responses/requests
    ├── Properties/
    │   ├── AssemblyInfo.cs              # Version information
    │   └── launchSettings.json          # Local development launch settings
    ├── Services/
    │   ├── LabelStore.cs                # Label storage and validation logic
    │   └── ViewerService.cs             # Session discovery, loading, and search logic
    └── wwwroot/
        ├── app.css                      # Shared global styles
        ├── css/
        │   ├── labels.css               # Styles for the label management screen
        │   └── viewer.css               # Styles for the main screen
        ├── icons/
        │   ├── claude-sessions-viewer.svg # App icon (Claude version)
        │   └── codex-sessions-viewer.svg  # App icon (Codex version)
        └── js/
            ├── labels.js                # Script for the label management screen
            └── viewer.js                # Script for the main screen
```

## ❗ This project is provided under the MIT License. See the LICENSE file for details.
