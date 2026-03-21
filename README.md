<p align="left">
  <a href="README_en.md"><img src="https://img.shields.io/badge/English Mode-blue.svg" alt="English"></a>
  <a href="README.md"><img src="https://img.shields.io/badge/日本語 モード-red.svg" alt="日本語"></a>
</p>

# ClaudeSessionsViewer

Claude Codeの履歴 を一覧・詳細表示して、検索することができるローカル Viewer です。覚えておきたい内容にラベルを貼り付けて、あとから検索することもできます。

- 本ツールは 日本語 / English / 简体中文 / 繁體中文 に対応しています。
- ご意見、ご要望はご遠慮なく issue に投稿ください。

## 画面構成

### メイン画面

![image](/image/00001.jpg)

### ラベル管理画面

![image](/image/00002.jpg)

### ショートカットキーリスト画面

![image](/image/00003.jpg)

⭐ このプロジェクトが役に立ったら、Starしてもらえると嬉しいです！

👀 更新を追いたい方はWatchもぜひ！

## 起動方法

Releasesにある`app-framework-dependent`フォルダをダウンロード後、解凍してから中にある`CodexSessionsViewer.exe`を実行してください。

※本ツールの実行には.NET 10 SDK または.NET 10 Runtimeが必要となります。入っているか分からない、またはインストールしないことを望む場合、`app-self-contained`フォルダをダウンロードしてください。

---

※srcからビルドを行う場合、以下のようにPower Shell スクリプトを実行してください。

- 非自己完結版(.NET 10 SDK または.NET 10 Runtimeをインストール済の場合)

```
.\publish.ps1 -CleanOutput
```

- 自己完結版(.NET 10 SDK または.NET 10 Runtimeのインストール状況不明、インストールしない場合)

```
.\publish.ps1 -SelfContained -CleanOutput
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
  - `project/path` / `開始日` / `終了日` / `イベント開始日時` / `イベント終了日時` / キーワード / `source` / セッションラベル / イベントラベルで絞り込み
  - `開始日` / `終了日` はブラウザ標準の `date` 入力、イベント日時は `date + time` の分割入力
  - イベント日時の時刻欄は、対応する日付を入れると有効化
  - キーワード検索は SQLite インデックスを使う全文検索
  - `project/path` 検索は `project` と `relative_path` の両方を対象にし、`-` / `/` / `\` を同一視して判定
  - `message` / `tool_result` / `queue` / `progress` / `notice` / `snippet` など、イベント本文として抽出された文字列を検索対象
  - `project/path` / 日時 / `source` / ラベル条件は常に AND 条件で評価
  - `AND/OR` 切替はキーワード欄内のみ
    - `AND`: スペース区切りキーワードをすべて含む
    - `OR`: スペース区切りキーワードのどれかを含む
- 右ペイン: 選択セッションのイベント時系列表示
  - 初回詳細読み込み時はローディング表示、手動 `Refresh` 時は詳細更新中オーバーレイを表示
  - 詳細ヘッダーに `source` ラベル（`Claude Code CLI` / `Claude Desktop`）を表示
  - 詳細ヘッダーは 4 段構成
    - 1 段目: 表示フィルター群、`Clear`、`Refresh`、2 段目 / 3 段目 / 4 段目をまとめて畳む `Hide` / `Show`
    - 2 段目: コピー、ラベル追加、選択コピー関連の操作ボタン
    - 3 段目: キーワード検索欄、`フィルター`、`検索`、`前へ`、`次へ`、`Keyword Clear`
    - 4 段目: 単一 `message` を起点として選ぶモード、起点解除、起点以前 / 以降のメッセージ表示
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
  - `イベント開始日時` / `イベント終了日時` で、右ペインに表示するイベント時系列を絞り込み可能
  - 右ペインのイベント日時フィルタも `date + time` の分割入力で、時刻欄は日付入力後に有効化
  - `Clear` ボタンで詳細側の表示フィルターをクリア
  - `Refresh` ボタンで選択中セッションだけを再取得
  - 「セッション再開コマンドコピー」ボタンで `claude --resume <セッションID>` をコピー
  - 「表示中メッセージコピー」ボタンで、現在の表示フィルター結果をまとめてコピー
  - セッションラベル表示と「セッションにラベル追加」
  - イベントごとのラベル表示 / 追加 / 削除
  - 各 `message` イベントに「コピー」ボタンを表示
  - 「選択モード」で `message` イベントごとにチェックを付けて、「選択コピー」でまとめてコピー可能
    - フィルター適用中でも、すでに選択済みの `message` は保持される
  - 「起点選択モード」で単一の `message` を選び、「起点以降のみ表示」 / 「起点以前のみ表示」でメッセージを絞り込み可能
  - `message`（`user` / `assistant`）、`tool_result`、`queue`、`progress`、`notice`、`snippet` などを表示
  - `user` は薄青背景、`assistant` は薄緑背景、system / notice 系はグレー系背景、tool 系は青緑系背景で表示
- ラベル管理
  - 右上の「ラベル管理」ボタンから別ウィンドウで開く
  - セッションラベル / イベントラベルを共通管理
  - ラベル色は `#hex` / `rgb(...)` / `oklch(...)` を直接入力、または色プリセットから選択可能

## ショートカットキー

入力欄にカーソルがある間は、ショートカットは実行されません。`Esc` でショートカット一覧やラベルピッカーを閉じるか、検索入力からカーソルを外せます。

| キー        | 動作                                                                           |
| ----------- | ------------------------------------------------------------------------------ |
| `F5`        | 表示中の一覧またはセッション詳細を更新                                         |
| `Shift + F` | 左ペインのフィルタ表示を切り替え                                               |
| `Shift + L` | 左ペインの `Clear` を実行                                                      |
| `/`         | 検索入力欄にフォーカス                                                         |
| `N`         | 詳細検索の次のヒットへ移動                                                     |
| `P`         | 詳細検索の前のヒットへ移動                                                     |
| `M`         | `path / cwd / time` のメタ表示を切り替え                                       |
| `[`         | 前のセッションを開く                                                           |
| `]`         | 次のセッションを開く                                                           |
| `1`         | 「ユーザー指示のみ表示」を切り替え                                             |
| `2`         | 「AIレスポンスのみ表示」を切り替え                                             |
| `3`         | 「各入力と最終応答のみ」を切り替え                                             |
| `4`         | 「表示順を逆にする」を切り替え                                                 |
| `Shift + D` | 右ペインの表示条件と操作状態をクリア                                           |
| `Shift + T` | 詳細操作の表示と非表示を切り替え                                               |
| `Shift + R` | セッション再開コマンドをコピー（`claude --resume <セッションID>`）             |
| `Shift + C` | 表示中メッセージをコピー                                                       |
| `Shift + S` | 選択モードの開始と終了を切り替え                                               |
| `Shift + X` | 選択中メッセージをコピー                                                       |
| `Shift + G` | 起点選択モードの開始と終了を切り替え                                           |
| `Shift + H` | 起点を解除                                                                     |
| `,`         | 起点以前のみ表示                                                               |
| `.`         | 起点以降のみ表示                                                               |
| `Esc`       | ショートカット一覧やラベル追加ポップアップを閉じ、検索入力欄からカーソルを外す |

## 重要な制約

- Claude Code CLI（JSONL）は構造化して表示できます。
- Claude Desktop（IndexedDB/LevelDB）については、以下の仕様・制約があります。

### Claude Desktop のデータ構造について

調査の結果、Claude Desktop が `%APPDATA%\Claude\IndexedDB\` に保存しているのは **送信前のチャット下書きメッセージのみ** であることが判明しました。

| 項目                 | 説明                                             |
| -------------------- | ------------------------------------------------ |
| 保存されるデータ     | チャット入力欄の下書き（未送信メッセージ）       |
| 保存されないデータ   | 送信済みの会話履歴（ユーザー発話・AI 応答）      |
| 送信済み会話の保存先 | Anthropic のサーバー側（ローカルには存在しない） |

本 Viewer は LevelDB ログファイル（`.log`）を正式にパースし、下書きメッセージを正確に表示します。

- LevelDB WriteBatch レコードをブロック単位でデコード
- Chromium IndexedDB の文字列キー（UTF-16BE）を復元
- Blink SerializedScriptValue のヘッダーをスキップし、UTF-16LE の JSON を抽出
- TipTap / ProseMirror ドキュメントツリーからプレーンテキストを収集

> **なぜ会話履歴が見えないのか**  
> Claude Desktop は claude.ai の Web アプリを Electron でラップしたものです。会話履歴は Anthropic のクラウドに保存され、ローカルの IndexedDB には同期されません。そのため、本 Viewer で表示できるのは現在入力中の下書きのみとなります。

## 補足

- ラベルデータは `.cache/label-store-claude.json` に保存されます。
- Claude Code / Claude Desktop の実データが 1 件も見つからない場合は、同梱の `sample-data/claude/projects` をダミーデータとして自動表示します。
- 明示的に参照先を変えたい場合は `CLAUDE_SESSIONS_DIR` または `SESSIONS_DIR` で Claude Code CLI のルートを指定できます。
- Windows 環境では `wsl.exe -l -q` を使って WSL ディストリを列挙し、各ディストリの `~/.claude/projects` も自動探索します。
- 自動検出対象のディストリを絞る場合は `CLAUDE_WSL_DISTROS` を指定できます（例: `Ubuntu;Debian`）。
- 大量ログ対策で一覧最大 `400` 件、イベント最大 `4000` 件に制限しています。
- Viewer はデフォルトでローカル専用 (`127.0.0.1`) で待ち受けます。

## ファイル構成

```text
.
├── .gitignore                           # ルートの除外設定
├── LICENSE                              # ライセンス
├── README.md                            # 日本語README
├── README_en.md                         # 英語README
├── publish.ps1                          # 配布用 publish スクリプト
├── image/
│   ├── 00001.jpg                        # README掲載用のメイン画面サンプル
│   ├── 00002.jpg                        # README掲載用のラベル管理画面サンプル
│   └── 00003.jpg                        # README掲載用のショートカット画面サンプル
└── src/
    ├── .cache/
    │   └── label-store-claude.json      # ラベル定義と紐付けの保存先
    ├── ClaudeSessionsViewer.sln         # ソリューション
    ├── ClaudeSessionsViewer.csproj      # ASP.NET Core / Blazor プロジェクト定義
    ├── Program.cs                       # アプリ起動、URL設定、APIエンドポイント定義
    ├── appsettings.json                 # 本番向け設定
    ├── appsettings.Development.json     # 開発向け設定
    ├── Components/
    │   ├── App.razor                    # HTMLルートと共通スクリプト読込
    │   ├── Routes.razor                 # ルーティング定義
    │   ├── _Imports.razor               # Razor 共通 using
    │   ├── Layout/
    │   │   ├── MainLayout.razor         # 共通レイアウト
    │   │   ├── MainLayout.razor.css     # 共通レイアウト用スタイル
    │   │   ├── ReconnectModal.razor     # 再接続モーダル UI
    │   │   ├── ReconnectModal.razor.css # 再接続モーダル用スタイル
    │   │   └── ReconnectModal.razor.js  # 再接続モーダル用スクリプト
    │   └── Pages/
    │       ├── Error.razor              # エラー画面
    │       ├── Home.razor               # メイン画面
    │       ├── Labels.razor             # ラベル管理画面
    │       └── NotFound.razor           # 404画面
    ├── Models/
    │   └── ViewerDtos.cs                # APIレスポンス/リクエスト用 DTO
    ├── Properties/
    │   ├── AssemblyInfo.cs              # バージョン情報
    │   └── launchSettings.json          # ローカル開発用起動設定
    ├── Services/
    │   ├── LabelStore.cs                # ラベル保存・検証ロジック
    │   └── ViewerService.cs             # セッション探索・読込・検索ロジック
    └── wwwroot/
        ├── app.css                      # 全体共通スタイル
        ├── css/
        │   ├── labels.css               # ラベル管理画面用スタイル
        │   └── viewer.css               # メイン画面用スタイル
        ├── icons/
        │   ├── claude-sessions-viewer.svg # アプリアイコン（Claude版）
        │   └── codex-sessions-viewer.svg  # アプリアイコン（Codex版）
        └── js/
            ├── labels.js                # ラベル管理画面用スクリプト
            └── viewer.js                # メイン画面用スクリプト
```

## ❗このプロジェクトは MIT ライセンスの下で提供されています。詳細は LICENSE ファイルをご覧ください。
