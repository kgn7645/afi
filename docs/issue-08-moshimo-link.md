# Issue #8 調査結果: もしもリンクHTMLのテンプレ自動生成

## 結論
**実現可能。** 「自分の `a_id`（成果ID）＋ 商品URL」だけで、もしも管理画面を使わずに
有効なかんたんリンクHTML（および素の成果追跡URL）を生成できることを確認した。

## 仕組み（bundle.js の解析）
かんたんリンクの描画スクリプト `https://dn.msmstatic.com/site/cardlink/bundle.js` を解析した結果：

- クリック時の成果追跡URLはスクリプトが以下の形で組み立てている：
  ```
  https://af.moshimo.com/af/c/click?a_id=<A_ID>&p_id=<P_ID>&pc_id=<PC_ID>&pl_id=<PL_ID>&url=<商品URL>
  ```
- **成果計上はアカウント固有の `a_id` で行われる**（本アカウント: `5633316`）。
- `p_id` / `pc_id` / `pl_id` は **プログラム共通の定数**（bundle.js にハードコードされた既定値）：

  | プログラム | p_id | pc_id | pl_id |
  |-----------|------|-------|-------|
  | rakuten   | 54   | 54    | 27059 |
  | amazon    | 170  | 185   | 27060 |
  | yahoo     | 1225 | 1925  | 27061 |

- かんたんリンクHTMLの `msmaflink({...})` ペイロードは、アカウント固定部（a_id 等）と
  商品ごとの可変部（商品名 `n` / 画像 `d`,`c_p`,`p` / 商品URL `u` / 要素ID `eid`）に分離できる。

## 検証（ラウンドトリップ）
実際に取得した本物のリンク（山善 除湿機）から商品データを抽出し、本実装で再生成したところ、
`msmaflink` のペイロードが**完全一致**した（`eid` を固定した場合）。a_id・p_id・pc_id・pl_id・s_n すべて一致。
→ `tests/test_smoke.py::test_moshimo_easylink_roundtrip`

## 実装
- `core/moshimo_link.py`
  - `build_click_url(a_id, product_url, program)` … 素の成果追跡URL
  - `build_easylink_html(...)` … かんたんリンクHTML（カード）
  - `build_rakuten_link_by_keyword(keyword)` … 楽天検索→リンク生成を一括
- `core/rakuten.py` … 楽天市場 商品検索API（無料）。キーワード→商品URL/画像/価格
- 設定: `.env` の `MOSHIMO_AID`（成果ID）, `RAKUTEN_APP_ID`（楽天APIキー）

## 使い方（例）
```python
from core import moshimo_link as ml
# 商品URLから直接（最小構成）
html = ml.build_easylink_html(a_id=5633316, name="商品名", product_url="https://item.rakuten.co.jp/shop/xxx/")
# キーワードから全自動（楽天API使用）
result = ml.build_rakuten_link_by_keyword("除湿機 ペルチェ式")  # {html, click_url, product}
```

## ⚠️ コンプライアンス上の注意（要確認）
- 本手法は「**利用者自身の a_id**」と「公開されたプログラム定数」のみを用い、
  もしもが生成するのと**同一の追跡URL**を再現するもの。
- ただし、リンクHTMLを管理画面を介さず自前生成・量産する運用が、
  **もしもアフィリエイトの利用規約上問題ないかは利用者自身で確認すること。**
  規約上NGの場合は Issue #1（取得済みリンクの保存・再利用ライブラリ）を正式な代替策とする。

## 残タスク（フォロー）
- [ ] 実リンクをサイトに設置し、`af.moshimo.com` 経由でクリック→成果計上されるか実地確認
- [ ] Amazon提携後、Amazonプログラムでも同様に生成できるか確認
- [ ] パイプライン（#1/#9）への組み込み（リンク未指定時に自動生成）
