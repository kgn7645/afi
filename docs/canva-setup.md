# Canva アイキャッチ連携セットアップ（任意）

アイキャッチを **Canvaのブランドテンプレート品質**で生成したい場合の設定。
未設定でも自動で **Pillow合成 → 商品画像** にフォールバックするため必須ではない。

> 前提: **Canva for Teams（有料）** が必要（ブランドテンプレート＋Autofill APIのため）。

## 1. 連携アプリを作成
1. https://www.canva.com/developers/ で Integration を作成
2. スコープを付与: `asset:write`, `design:content:write`, `design:content:read`,
   `design:meta:read`, `brandtemplate:meta:read`, `brandtemplate:content:read`
3. **Redirect URL** に `http://127.0.0.1:8080/callback` を登録（下記ヘルパーと一致させる）
4. Client ID / Client Secret を控える

## 1.5 リフレッシュトークンを取得（ヘルパーで自動）
`.env` に `CANVA_CLIENT_ID` / `CANVA_CLIENT_SECRET` を設定してから、**手元の（ブラウザが開ける）ターミナル**で:
```
python scripts/canva_auth.py
```
ブラウザでCanvaの認可画面 → 「許可」→ 自動でトークン取得し `data/canva_token.json` に保存。
表示された `CANVA_REFRESH_TOKEN=...` を `.env` に貼ってもよい（トークンは利用ごとに回転し、最新が自動保存される）。

## 2. ブランドテンプレートを用意
1. Canvaで 1200×630 のデザインを作成し、**ブランドテンプレート**として保存
2. 差し込み用の要素に名前を付ける:
   - テキスト枠 → `headline`（キャッチコピー）
   - 画像枠 → `product`（商品画像）
3. テンプレートID（`brand_template_id`）を控える
   - 名前は `config.yaml > canva.text_field / image_field` で変更可

## 3. .env に設定
```
CANVA_CLIENT_ID=xxxx
CANVA_CLIENT_SECRET=xxxx
CANVA_REFRESH_TOKEN=xxxx
```

## 4. config.yaml を有効化
```yaml
canva:
  enabled: true
  brand_template_id: "DAF..."   # ブランドテンプレID
  text_field: "headline"
  image_field: "product"
```

## 5. 動作
- パイプラインは `canva.available()` が真なら **Canvaで生成**を試行。
- 失敗（トークン期限切れ・テンプレ不一致・レート制限・ネット断）時は警告を出して
  **Pillow合成**に自動フォールバックするため、投稿は止まらない。

## 注意
- cron等のヘッドレス運用ではリフレッシュトークンの回転に追従する必要がある
  （本実装は `data/canva_token.json` に最新トークンを保存して追従）。
- 長期間未使用だとリフレッシュトークンが失効する場合がある→その時は再連携。
