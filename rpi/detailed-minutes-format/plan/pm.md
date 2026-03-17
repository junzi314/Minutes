# PM Plan: 詳細議事録フォーマット + トランスクリプト添付

## Goal

ユーザーが会議の全体像を素早く把握しつつ、必要に応じて詳細やトランスクリプトを参照できる、Geminiメモ水準の議事録出力を提供する。

## Success Metrics

1. Discord投稿に議事録（まとめ + 詳細 + 次のステップ）とトランスクリプトの2ファイルが添付される
2. 詳細セクションの各項目にタイムスタンプ参照が含まれる
3. 長時間会議（1h+）でも情報が途切れない（max_tokens 8192）
4. 既存のEmbed表示（サマリー/決定事項/話者統計）が壊れない

## Scope

### In Scope
- プロンプトテンプレートのGeminiメモ風書き換え
- トランスクリプトのMarkdown整形 + 添付
- max_tokens 引き上げ（4096 → 8192）
- Embed正規表現の新フォーマット対応（必要な場合のみ）

### Out of Scope
- Embed UIの大幅変更
- トランスクリプトのDiscordメッセージ分割投稿
- 複数テンプレートの切り替えUI

## Rollout

- `config.yaml` の `include_transcript: true` で有効化
- 既存の `include_transcript: false` 設定で後方互換維持
