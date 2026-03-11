<p align="left">
  <a href="README_en.md"><img src="https://img.shields.io/badge/English Mode-blue.svg" alt="English"></a>
  <a href="README.md"><img src="https://img.shields.io/badge/日本語 モード-red.svg" alt="日本語"></a>
</p>

# ClaudeSessionsViewer

Claude のチャット履歴を検索・表示するローカル Viewer です。  
以下 2 系統の保存先を探索します。

- `~/.claude/projects/`（Claude Code CLI / JSONL）
- `%APPDATA%\Claude\IndexedDB\`（Claude Desktop / LevelDB）

![image](/image/00001.jpg)

## 前提条件

- Python 3（`py -3` または `python` / `python3` コマンドが利用可能）
- Web ブラウザ（Edge / Chrome など）

Python 3 が未インストールの場合（Windows / winget）:

```powershell
winget install -e --id Python.Python.3.12
```

インストール確認:

```powershell
py -3 --version
```

## Windows から起動

- `scripts\windows\launch_viewer.bat`
- `scripts\windows\stop_viewer.bat`

`launch_viewer.bat` は Windows 上で `viewer.py` を起動し、ブラウザを開きます。

既定 URL:

```text
http://127.0.0.1:8767
```

## 直接起動（Python）

```powershell
python viewer.py
```

または:

```bash
python3 viewer.py
```

## デフォルト参照先

- Claude Code CLI
  - `~/.claude/projects`
  - `%USERPROFILE%\.claude\projects`
  - `WIN_HOME\.claude\projects`（`WIN_HOME` 指定時）
  - `/mnt/c/Users/*/.claude/projects`
  - `\\wsl.localhost\<distro>\home\*\.claude\projects`（Windows 起動時に WSL ディストリを自動検出）
- Claude Desktop
  - `%APPDATA%\Claude\IndexedDB`
  - `%USERPROFILE%\AppData\Roaming\Claude\IndexedDB`
  - `WIN_HOME\AppData\Roaming\Claude\IndexedDB`（`WIN_HOME` 指定時）
  - `/mnt/c/Users/*/AppData/Roaming/Claude/IndexedDB`

任意の Claude Code CLI ディレクトリを使う場合:

```powershell
$env:CLAUDE_SESSIONS_DIR = 'C:\path\to\.claude\projects'
python viewer.py
```

補足:

- `SESSIONS_DIR` でも上書きできます。
- 複数指定は `os.pathsep` 区切り（Windows は `;`, Unix/WSL は `:`）です。
- Windows 版 `viewer.py` は `wsl.exe -l -q` を使って WSL ディストリを列挙し、各ディストリの `~/.claude/projects` も自動探索します。
- 自動検出対象のディストリを絞る場合は `CLAUDE_WSL_DISTROS` を指定できます（例: `Ubuntu;Debian`）。

## 画面機能

- 左ペイン: セッション一覧（最新順）
- 一覧にセッション `source` ラベル（`CLI(JSONL)` / `Desktop(LevelDB)`）を表示
- 左上 filter: `project/path` / 日付範囲 / キーワード / `source` で絞り込み
- 検索は一部一致（部分一致）。`project` / `relative_path` / 最初のユーザー入力を対象
- `project/path` 検索は `project` と `relative_path` の両方を対象にし、`-` / `/` / `\` を同一視して判定
- `project/path` / 日付範囲 / キーワード / `source` は常に AND 条件で評価
- `AND/OR` 切替はキーワード欄内のみ
  - `AND`: スペース区切りキーワードをすべて含む
  - `OR`: スペース区切りキーワードのどれかを含む
- 右ペイン: 選択セッションのイベント時系列表示
  - 詳細ヘッダーに `source` ラベル（`Claude Code CLI` / `Claude Desktop`）を表示
  - 表示オプション
    - 「ユーザー指示のみ表示」
    - 「AIレスポンスのみ表示」
    - 「表示順を逆にする」
  - 「セッション再開コマンドコピー」ボタンで `claude --resume <セッションID>` をコピー
  - `message`（`user` / `assistant`）および `function_call` / `function_output` / `agent_update` を表示
  - `user` は薄青背景、`assistant` は薄緑背景、`AGENTS.md` / `environment_context` などの実行コンテキストはグレー背景で表示

## 重要な制約

- Claude Code CLI（JSONL）は構造化して表示できます。
- Claude Desktop（IndexedDB/LevelDB）については、以下の仕様・制約があります。

### Claude Desktop のデータ構造について

調査の結果、Claude Desktop が `%APPDATA%\Claude\IndexedDB\` に保存しているのは **送信前のチャット下書きメッセージのみ** であることが判明しました。

| 項目 | 説明 |
|------|------|
| 保存されるデータ | チャット入力欄の下書き（未送信メッセージ） |
| 保存されないデータ | 送信済みの会話履歴（ユーザー発話・AI 応答） |
| 送信済み会話の保存先 | Anthropic のサーバー側（ローカルには存在しない） |

本 Viewer は LevelDB ログファイル（`.log`）を正式にパースし、下書きメッセージを正確に表示します。

- LevelDB WriteBatch レコードをブロック単位でデコード
- Chromium IndexedDB の文字列キー（UTF-16BE）を復元
- Blink SerializedScriptValue のヘッダーをスキップし、UTF-16LE の JSON を抽出
- TipTap / ProseMirror ドキュメントツリーからプレーンテキストを収集

> **なぜ会話履歴が見えないのか**
> Claude Desktop は claude.ai の Web アプリを Electron でラップしたものです。会話履歴は Anthropic のクラウドに保存され、ローカルの IndexedDB には同期されません。そのため、本 Viewer で表示できるのは現在入力中の下書きのみとなります。

## 環境変数

- `HOST`: バインドアドレス（既定 `127.0.0.1`）
- `PORT`: ポート（既定 `8767`）
- `CLAUDE_SESSIONS_DIR` / `SESSIONS_DIR`: Claude Code CLI の JSONL ルートを上書き

## ❗このプロジェクトは MIT ライセンスの下で提供されています。詳細は LICENSE ファイルをご覧ください。
