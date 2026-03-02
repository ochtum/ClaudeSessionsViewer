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

## 主な機能

- セッション一覧のキーワード検索（AND/OR）
- `project/path` 絞り込み（部分一致）
- 日付範囲フィルタ
- ソース種別フィルタ（Claude Code CLI / Claude Desktop）
- セッション詳細表示（ユーザー発話のみ表示、逆順表示）

`project/path` 検索仕様:

- `project` と `relative_path` の両方を対象に検索します。
- `-` / `/` / `\` を同一視して検索できます。
- 例: `C:\junichi\takeda\source` / `C:/junichi/takeda/source` / `C--junichi-takeda-source`

表示仕様:

- `project` 表示は Windows パス形式を優先（`C:\...`）。
- `C--foo-bar...` の slug 形式も `C:\foo\bar\...` に正規化して表示します。

## 重要な制約

- Claude Code CLI（JSONL）は構造化して表示できます。
- Claude Desktop（IndexedDB/LevelDB）はバイナリ形式のため、現状は文字列/JSON スニペット抽出で表示します。
  - UTF-8/UTF-16LE の両方を走査し、入れ子 JSON をバランス解析で抽出します。
  - 完全な履歴復元ではありません。
  - 専用デコーダを将来的に組み込む余地があります。

## 環境変数

- `HOST`: バインドアドレス（既定 `127.0.0.1`）
- `PORT`: ポート（既定 `8767`）
- `CLAUDE_SESSIONS_DIR` / `SESSIONS_DIR`: Claude Code CLI の JSONL ルートを上書き

## ❗このプロジェクトは MIT ライセンスの下で提供されています。詳細は LICENSE ファイルをご覧ください。
