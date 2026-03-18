# UX Design: Transcript Glossary (用語辞書)

Whisper文字起こしの頻出誤認識を、ギルド単位のユーザー定義辞書で自動補正する機能。
Discordスラッシュコマンドで辞書のCRUD操作を行い、パイプライン実行時に自動適用される。

---

## 1. ユーザーストーリーと受け入れ基準

### US-1: 辞書にエントリを追加する

**As** サーバー管理者, **I want** よくある誤認識と正しい表記のペアを辞書に登録したい, **so that** 以降の議事録で同じ誤認識が自動的に補正される。

**受け入れ基準:**
- `/minutes glossary add <wrong> <correct>` で新規エントリを追加できる。
- 追加成功時、ephemeralメッセージで確認: 「{wrong}」→「{correct}」を辞書に追加しました。
- 既存エントリと同じ `wrong` を指定した場合、上書き更新される。
- 上書き時、ephemeralメッセージで旧値を表示: 「{wrong}」→「{correct}」に更新しました。(旧: {old_correct})
- `manage_guild` 権限がないユーザーはコマンドを実行できない。

### US-2: 辞書からエントリを削除する

**As** サーバー管理者, **I want** 不要になった辞書エントリを削除したい, **so that** 辞書が整理された状態を維持できる。

**受け入れ基準:**
- `/minutes glossary remove <wrong>` で既存エントリを削除できる。
- 削除成功時、ephemeralメッセージで確認: 「{wrong}」を辞書から削除しました。
- 存在しない `wrong` を指定した場合、ephemeralメッセージでエラー: 「{wrong}」は辞書に登録されていません。
- `manage_guild` 権限がないユーザーはコマンドを実行できない。

### US-3: 辞書の一覧を確認する

**As** サーバー管理者, **I want** 現在登録されている辞書エントリを一覧表示したい, **so that** 登録状況を把握して管理できる。

**受け入れ基準:**
- `/minutes glossary list` でギルドの辞書全体をDiscord Embedで表示する。
- エントリがない場合、空状態メッセージを表示する。
- レスポンスはephemeral（コマンド実行者のみに表示）。
- `manage_guild` 権限がないユーザーはコマンドを実行できない。

### US-4: 議事録生成時に辞書が自動適用される

**As** サーバーメンバー, **I want** 登録済みの辞書が議事録生成時に自動で適用されてほしい, **so that** 操作なしで正しい表記の議事録を得られる。

**受け入れ基準:**
- パイプラインのtranscribe後・merge前に辞書が自動適用される。
- 大文字・小文字を区別しないマッチング（デフォルト）。
- 辞書が空の場合、何もせずスキップする（パフォーマンス影響なし）。
- ユーザーによる追加操作は不要。

---

## 2. ユーザーフロー

### Flow 1: 辞書にエントリを追加（新規）

```
1. ユーザーが入力:  /minutes glossary add wrong:ツーニック correct:TOONIQ
2. Discordがインタラクションをbotに送信。
3. bot が manage_guild 権限を検証。
4. bot が state_store.get_guild_glossary(guild_id) で既存辞書を取得。
5. 「ツーニック」が辞書に存在しない → 新規追加。
6. state_store.set_guild_glossary(guild_id, updated_glossary) で永続化。
7. ephemeralメッセージで応答:
     「ツーニック」→「TOONIQ」を辞書に追加しました。
```

### Flow 2: 辞書エントリを上書き更新

```
1. ユーザーが入力:  /minutes glossary add wrong:ツーニック correct:Tooniq
2. Discordがインタラクションをbotに送信。
3. bot が manage_guild 権限を検証。
4. bot が既存辞書を取得 → 「ツーニック」が既に存在（旧値: TOONIQ）。
5. 値を上書き更新して永続化。
6. ephemeralメッセージで応答:
     「ツーニック」→「Tooniq」に更新しました。（旧: TOONIQ）
```

### Flow 3: 辞書からエントリを削除（成功）

```
1. ユーザーが入力:  /minutes glossary remove wrong:ツーニック
2. Discordがインタラクションをbotに送信。
3. bot が manage_guild 権限を検証。
4. bot が既存辞書を取得 → 「ツーニック」が存在する。
5. エントリを削除して永続化。
6. ephemeralメッセージで応答:
     「ツーニック」を辞書から削除しました。
```

### Flow 4: 辞書からエントリを削除（該当なし）

```
1. ユーザーが入力:  /minutes glossary remove wrong:フィグマ
2. Discordがインタラクションをbotに送信。
3. bot が manage_guild 権限を検証。
4. bot が既存辞書を取得 → 「フィグマ」は存在しない。
5. ephemeralメッセージで応答:
     「フィグマ」は辞書に登録されていません。
```

### Flow 5: 辞書一覧の表示（エントリあり）

```
1. ユーザーが入力:  /minutes glossary list
2. Discordがインタラクションをbotに送信。
3. bot が manage_guild 権限を検証。
4. bot が既存辞書を取得 → エントリが1件以上。
5. Discord Embedを構築して応答（ephemeral）:
     Title:  用語辞書
     Color:  0x5865F2
     Description:
       ツーニック → TOONIQ
       フィグマ → Figma
       たなかさん → 田中さん
     Footer: {N}件のエントリ | /minutes glossary add で追加
```

### Flow 6: 辞書一覧の表示（空）

```
1. ユーザーが入力:  /minutes glossary list
2. Discordがインタラクションをbotに送信。
3. bot が manage_guild 権限を検証。
4. bot が既存辞書を取得 → 空。
5. ephemeralメッセージで応答:
     辞書にエントリがありません。`/minutes glossary add` で追加してください。
```

### Flow 7: 権限不足

```
1. manage_guild 権限を持たないユーザーが任意の glossary コマンドを実行。
2. discord.py の権限チェックデコレータが拒否。
3. ephemeralメッセージで応答:
     辞書の操作には「サーバー管理」権限が必要です。
```

### Flow 8: 自動補正（パイプライン内、ユーザー操作なし）

```
1. Craig録音終了 / /minutes process / Drive監視 によりパイプライン起動。
2. transcription ステージ完了 → Segment リスト取得。
3. config.transcript_glossary.enabled が true かつ辞書が空でない場合:
     a. state_store.get_guild_glossary(guild_id) で辞書を取得。
     b. apply_glossary(segments, glossary) で全セグメントのテキストを置換。
     c. 大文字・小文字を区別しないマッチング（re.sub + re.IGNORECASE）。
4. 補正済みセグメントが merge ステージに渡される。
5. ユーザーには補正後の議事録のみが表示される（補正の過程は見えない）。
```

---

## 3. UIコンポーネント

### 3.1 辞書一覧 Embed

| プロパティ | 値 |
|-----------|-----|
| Type | Discord Embed |
| Title | 用語辞書 |
| Color | 0x5865F2 (Discord blurple) |
| Description | エントリを改行区切りで `{wrong} → {correct}` の形式で列挙 |
| Footer | `{N}件のエントリ | /minutes glossary add で追加` |
| Ephemeral | Yes |

表示例（エントリあり）:

```
+--------------------------------------------------+
| 用語辞書                                          |
|                                                  |
| ツーニック → TOONIQ                                |
| フィグマ → Figma                                   |
| たなかさん → 田中さん                                |
| えーあい → AI                                      |
|                                                  |
| 4件のエントリ | /minutes glossary add で追加         |
+--------------------------------------------------+
```

**エントリ数が多い場合（25件超）**: Discord Embedの文字数制限（4096文字）に収まるようにDescription内で列挙する。1エントリあたり平均40文字と仮定すると、約100エントリまで1つのEmbedに収まる。上限を超えた場合は末尾に「...他 {残り件数} 件」と表示し、切り捨てる。

### 3.2 ephemeralメッセージ一覧

全てのレスポンスはephemeral（コマンド実行者のみに表示）。

| 場面 | メッセージ |
|------|----------|
| 追加成功（新規） | 「{wrong}」→「{correct}」を辞書に追加しました。 |
| 追加成功（上書き） | 「{wrong}」→「{correct}」に更新しました。（旧: {old_correct}） |
| 削除成功 | 「{wrong}」を辞書から削除しました。 |
| 削除失敗（該当なし） | 「{wrong}」は辞書に登録されていません。 |
| 一覧（空） | 辞書にエントリがありません。\`/minutes glossary add\` で追加してください。 |
| 権限不足 | 辞書の操作には「サーバー管理」権限が必要です。 |

### 3.3 スラッシュコマンドパラメータ

| コマンド | パラメータ | 型 | 必須 | 説明 |
|---------|-----------|-----|------|------|
| `/minutes glossary add` | `wrong` | String | Yes | 誤認識テキスト（置換元） |
| `/minutes glossary add` | `correct` | String | Yes | 正しい表記（置換先） |
| `/minutes glossary remove` | `wrong` | String | Yes | 削除する誤認識テキスト |
| `/minutes glossary list` | (なし) | - | - | - |

---

## 4. 状態管理

### 4.1 正常状態

| 状態 | トリガー | 表示 |
|------|---------|------|
| 初回利用（辞書空） | `/minutes glossary list` | 空状態メッセージ |
| エントリあり | `/minutes glossary list` | Embedにエントリ一覧 |
| エントリ追加 | `/minutes glossary add` | 確認メッセージ（新規 or 上書き） |
| エントリ削除 | `/minutes glossary remove` | 確認メッセージ |
| 自動補正適用 | パイプライン実行 | ユーザーには不可視（補正済み議事録のみ表示） |

### 4.2 空状態

| 状態 | 条件 | 振る舞い |
|------|------|---------|
| 辞書未作成 | ギルドで一度もadd/removeを実行していない | パイプラインで辞書適用をスキップ |
| 辞書が空 | 全エントリを削除した後 | 辞書未作成と同じ振る舞い |
| 一覧表示時の空 | `/minutes glossary list` 実行時にエントリ0件 | ガイダンス付き空状態メッセージ |

### 4.3 エラー状態

| 状態 | トリガー | 表示 |
|------|---------|------|
| 権限不足 | `manage_guild` 権限なしでコマンド実行 | ephemeral: 「サーバー管理」権限が必要です |
| 削除対象なし | 存在しない `wrong` を指定して remove | ephemeral: 登録されていません |
| guild_settings.json 破損 | ファイル読み込み失敗 | `_load_json` が `{}` を返し、空辞書として動作。ユーザーには辞書が空として表示される |
| ディスク書き込み失敗 | state_store の永続化エラー | インメモリ状態は保持。ユーザーにはエラーを見せず、ログに WARNING を記録 |

---

## 5. アクセシビリティ

### 5.1 コマンドの発見性

- 全コマンドは既存の `/minutes` グループ配下に配置。`/minutes glossary` と入力すると、Discordのオートコンプリートで `add` / `remove` / `list` サブコマンドが表示される。
- 空状態メッセージにコマンドのヒント（`/minutes glossary add` で追加してください）を含め、次のアクションを案内する。
- Embedのフッターにもコマンドのヒントを表示する。

### 5.2 キーボード操作

- Discordスラッシュコマンドは完全にキーボードで操作可能。`/minutes glossary` と入力し、矢印キーでサブコマンドを選択、Tabでパラメータ入力に遷移できる。
- ボタンやリアクション等のマウス操作を必要とするインタラクションは使用しない。

### 5.3 スクリーンリーダー対応

- Embedのタイトル・Description・フッターは全てプレーンテキストで構成。スクリーンリーダーが解釈可能。
- 「→」記号は辞書エントリの方向性を示すテキストとして使用。装飾目的の絵文字やアイコンは使用しない。
- 確認メッセージの「」（鉤括弧）は日本語テキストの引用を明確にし、読み上げ時にパラメータ値の境界を示す。

### 5.4 コントラストと可読性

- Embedカラー 0x5865F2 (Discord blurple) はライト・ダークテーマの両方で十分なコントラストを確保。
- エントリ一覧は1行1エントリの改行区切りで、視覚的に分離されている。
- コードブロック内のコマンド表示（バッククォート）はDiscordのテーマに関わらず等幅フォントで表示され、コマンド文字列を地の文と明確に区別する。

### 5.5 エラー回復ガイダンス

- 全てのエラーメッセージは次のアクションを含む:
  - 権限不足 → 「サーバー管理」権限という具体的な権限名を明示。
  - 削除対象なし → 存在しないキーをそのまま表示し、ユーザーがタイプミスを確認できるようにする。
  - 空状態 → `/minutes glossary add` コマンドを案内。

---

## 6. データフロー

```
                     Discord slash commands
                     (/minutes glossary add/remove/list)
                              |
                              v
                    bot.py (権限検証 + コマンド処理)
                              |
                              v
                    state_store.get/set_guild_glossary()
                              |
                              v
                    state/guild_settings.json
                      { guild_id: { "glossary": { wrong: correct, ... } } }


                     パイプライン実行時:

    transcriber.py             glossary.py               merger.py
    (文字起こし)         (辞書適用: apply_glossary)       (話者統合)
         |                        |                         |
         v                        v                         v
    Segment[]  ──────>  補正済み Segment[]  ──────>  統合トランスクリプト
                              ^
                              |
                    state_store.get_guild_glossary()
```
