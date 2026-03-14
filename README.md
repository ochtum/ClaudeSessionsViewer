<p align="left">
  <a href="README_en.md"><img src="https://img.shields.io/badge/English Mode-blue.svg" alt="English"></a>
  <a href="README.md"><img src="https://img.shields.io/badge/日本語 モード-red.svg" alt="日本語"></a>
</p>

# ClaudeSessionsViewer

Claude のチャット履歴を検索・表示するローカル Viewer です。  
以下 2 系統の保存先を探索します。

- `~/.claude/projects/`（Claude Code CLI / JSONL）
- `%APPDATA%\Claude\IndexedDB\`（Claude Desktop / LevelDB）

## 画面構成

### メイン画面

![image](/image/00001.jpg)

### ラベル管理画面

![image](/image/00002.jpg)

## ディレクトリ構成

```text
.
├─ viewer.py
└─ scripts
   └─ windows
      ├─ launch_viewer.bat
      └─ stop_viewer.bat
```

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

## 起動方法

### Windows からワンクリック起動（バッチ）

- `scripts\windows\launch_viewer.bat`
- `scripts\windows\stop_viewer.bat`

`launch_viewer.bat` は Windows 上で `viewer.py` を起動し、ブラウザを開きます。

起動後、ブラウザで以下を開きます。

```text
http://127.0.0.1:8767
```

### 直接起動（Python）

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

## オプション

デフォルト以外の Claude Code CLI ディレクトリを使う場合は `CLAUDE_SESSIONS_DIR` を設定します。  
`SESSIONS_DIR` でも上書きできます。複数指定は `os.pathsep` 区切り（Windows は `;`, Unix/WSL は `:`）です。

```powershell
$env:CLAUDE_SESSIONS_DIR = 'C:\path\to\.claude\projects'
python viewer.py
```

待ち受けアドレスを変更する場合は `HOST` を設定します。

```powershell
$env:HOST = '0.0.0.0'
python viewer.py
```

## 画面機能

- 左ペイン: セッション一覧（最新順）
  - 一覧にセッション `source` ラベル（`Claude Code CLI` / `Claude Desktop`）とセッションラベルを表示
  - 初回起動時は一覧のローディング状態を表示
  - `Reload` ボタンで一覧を再読み込み
    - 手動 `Reload` 時は一覧の更新中オーバーレイとボタン状態を表示
  - `Clear` ボタンで左ペインの検索条件を初期化
  - `Hide` / `Show` ボタンで検索条件欄を折りたたみ / 展開可能
  - 縦表示時はヘッダー右上の「一覧を隠す / 一覧を表示」ボタンで左ペイン全体を切り替え可能
- 左上 filter
  - `project/path` / 日付範囲 / キーワード / `source` / セッションラベル / イベントラベルで絞り込み
  - キーワード検索は SQLite インデックスを使う全文検索
  - `project/path` 検索は `project` と `relative_path` の両方を対象にし、`-` / `/` / `\` を同一視して判定
  - `message` / `tool_result` / `queue` / `progress` / `notice` / `snippet` など、イベント本文として抽出された文字列を検索対象
  - `project/path` / 日付範囲 / `source` / ラベル条件は常に AND 条件で評価
  - `AND/OR` 切替はキーワード欄内のみ
    - `AND`: スペース区切りキーワードをすべて含む
    - `OR`: スペース区切りキーワードのどれかを含む
- 右ペイン: 選択セッションのイベント時系列表示
  - 初回詳細読み込み時はローディング表示、手動 `Refresh` 時は詳細更新中オーバーレイを表示
  - 詳細ヘッダーに `source` ラベル（`Claude Code CLI` / `Claude Desktop`）を表示
  - 詳細ヘッダーは 3 段構成
    - 1 段目: 表示フィルター群、`Refresh`、2 段目 / 3 段目をまとめて畳む `Hide` / `Show`
    - 2 段目: コピー、ラベル追加、選択コピー関連の操作ボタン
    - 3 段目: キーワード検索欄、`フィルター`、`検索`、`前へ`、`次へ`、`Keyword Clear`
  - 表示オプション
    - 「ユーザー指示のみ表示」
    - 「AIレスポンスのみ表示」
    - 「各入力と最終応答のみ」
      - 各ターンで `user` の入力 1 件と、次の `user` が来るまでの最後の `assistant` 1 件だけを表示
    - 「表示順を逆にする」
    - `event label: all` フィルタ
  - キーワード検索
    - `フィルター`: キーワードを含むイベントだけを表示
    - `検索`: 一致箇所をハイライトし、`前へ` / `次へ` で候補間を移動
    - `Keyword Clear`: 入力欄、フィルター、検索状態をまとめて解除
    - AND / OR ではなく、入力した文字列そのままの部分一致で判定
    - 検索対象は表示中イベントの本文全体
  - `Refresh` ボタンで選択中セッションだけを再取得
  - 「セッション再開コマンドコピー」ボタンで `claude --resume <セッションID>` をコピー
  - 「表示中メッセージコピー」ボタンで、現在の表示フィルター結果をまとめてコピー
  - セッションラベル表示と「セッションにラベル追加」
  - イベントごとのラベル表示 / 追加 / 削除
  - 各 `message` イベントに「コピー」ボタンを表示
  - 「選択モード」で `message` イベントごとにチェックを付けて、「選択コピー」でまとめてコピー可能
    - フィルター適用中でも、すでに選択済みの `message` は保持される
  - `message`（`user` / `assistant`）、`tool_result`、`queue`、`progress`、`notice`、`snippet` などを表示
  - `user` は薄青背景、`assistant` は薄緑背景、system / notice 系はグレー系背景、tool 系は青緑系背景で表示
- ラベル管理
  - 右上の「ラベル管理」ボタンから別ウィンドウで開く
  - セッションラベル / イベントラベルを共通管理
  - ラベル色は `#hex` / `rgb(...)` / `oklch(...)` を直接入力、または色プリセットから選択可能

## 重要な制約

- Claude Code CLI（JSONL）は構造化して表示できます。
- Claude Desktop（IndexedDB/LevelDB）については、以下の仕様・制約があります。

### Claude Desktop のデータ構造について

調査の結果、Claude Desktop が `%APPDATA%\Claude\IndexedDB\` に保存しているのは **送信前のチャット下書きメッセージのみ** であることが判明しました。

| 項目 | 説明 |
| ---- | ---- |
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

## 補足

- 検索インデックスは `.cache/search_index.sqlite3` に保存され、変更のあったセッションだけ差分更新します。
- Windows 版 `viewer.py` は `wsl.exe -l -q` を使って WSL ディストリを列挙し、各ディストリの `~/.claude/projects` も自動探索します。
- 自動検出対象のディストリを絞る場合は `CLAUDE_WSL_DISTROS` を指定できます（例: `Ubuntu;Debian`）。
- 大量ログ対策で一覧最大 `400` 件、イベント最大 `4000` 件に制限しています。
- Viewer はデフォルトでローカル専用 (`127.0.0.1`) で待ち受けます。

## ❗このプロジェクトは MIT ライセンスの下で提供されています。詳細は LICENSE ファイルをご覧ください。
