# 記事の承認Webアプリ（Issue #12）

自動生成された下書きを**スマホ/PCのブラウザ**で確認し、**ワンタップで公開/却下**する画面。
既存FastAPIアプリ（`app.py`）の `/review` に同居。WordPress REST APIでステータスを更新する。

## 画面
- `/review` … 承認待ち（下書き）一覧。サムネ・タイトル・抜粋・**QA結果バッジ**
- `/review/{id}` … 本文プレビュー＋QA指摘＋公開/却下ボタン
- 「公開」= WP status を publish に / 「却下」= ゴミ箱（復元可能）

## 必要な環境変数（.env）
```
WP_BASE_URL=https://ouchibase.com
WP_USERNAME=...
WP_APP_PASSWORD=...        # WordPressのアプリケーションパスワード
REVIEW_PASSWORD=任意の合言葉   # これが未設定だと承認画面は無効
SESSION_SECRET=ランダム文字列   # Cookie署名用（Renderでは自動生成）
```

## ローカルで動かす
```
.venv/bin/python -m uvicorn app:app --reload
# → http://127.0.0.1:8000/review
```

## Render（無料）にデプロイ＝「どこからでも」アクセス
1. このリポジトリをGitHub連携で **Render の New Web Service** に接続
2. `render.yaml` が読み込まれる（Python / `uvicorn app:app`）
3. 環境変数 `WP_BASE_URL / WP_USERNAME / WP_APP_PASSWORD / REVIEW_PASSWORD` を設定
   （`SESSION_SECRET` は自動生成）
4. 発行されたURL `https://<name>.onrender.com/review` をスマホのホーム画面に追加

> 補足
> - 認証はパスワード＋HMAC署名Cookie（7日有効）。`noindex` 付き。
> - 無料プランはアイドルでスリープ→初回アクセスが数秒遅い場合あり。
> - 生成バッチ（cron）はXserver側のまま。本アプリは**承認専用**でWPを叩くだけ。
