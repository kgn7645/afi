# エックスサーバー デプロイ手順 — Issue #6

生成ツールをエックスサーバーに設置し、cronで無人運用するまでの実機手順。
WordPressと同居するが、ツールはREST APIで通信するだけで役割は分離される。

> 所要：30〜60分。SSH操作に慣れていれば短い。各ステップは順番に実施する。

## 事前に用意するもの
- エックスサーバーの**サーバーパネル**ログイン情報
- 各APIの値（このリポジトリの `.env.example` 参照）
  - `GEMINI_API_KEY` / `WP_BASE_URL` / `WP_USERNAME` / `WP_APP_PASSWORD`
  - `MOSHIMO_AID` / `RAKUTEN_APP_ID` / `RAKUTEN_ACCESS_KEY` / `INDEXNOW_*`

---

## STEP 1. SSHを有効化して接続
1. サーバーパネル → **「SSH設定」** → 状態を「ON」
2. 「公開鍵認証用鍵ペアの生成」で鍵を作成し、秘密鍵(`.key`)をダウンロード
   （または手元の公開鍵を登録）
3. 手元のMacで秘密鍵を配置・権限設定し、接続：
   ```bash
   mkdir -p ~/.ssh && mv ~/Downloads/*.key ~/.ssh/xserver.key
   chmod 600 ~/.ssh/xserver.key
   # ポートは10022。アカウント名・サーバー番号はサーバーパネルの「アカウント情報」で確認
   ssh -i ~/.ssh/xserver.key -p 10022 <アカウント名>@<サーバー番号>.xserver.jp
   ```
   接続できたら STEP 2 へ。

## STEP 2-4. リポジトリ取得＋Python＋依存（セットアップスクリプト）
> エックスサーバー標準Pythonは3.6.8と古いため、コンパイル不要の事前ビルド版
> Python3.11を導入する。以下のスクリプトが Python導入→venv→依存インストールまで自動で行う。

```bash
cd ~
git clone https://github.com/kgn7645/afi.git
cd afi
bash scripts/setup_xserver.sh
```
- 末尾に「次の手順」と、楽天APIに登録すべき送信元IPが表示される。
- 失敗時は「トラブル」を参照。

## STEP 5. シークレット(.env)を作成
```bash
cp .env.example .env
nano .env     # もしくは vi。各値を貼り付けて保存
chmod 600 .env
```
- Macの `.env` と同じ値を入れる（**Gitには上がらない**ので手動コピー）。

## STEP 6. このサーバーの送信元IPを楽天APIへ許可登録
```bash
curl -s https://api.ipify.org ; echo
```
- 表示されたIPを **楽天ウェブサービスのアプリ設定 → 許可IP** に追加する。
  （Macのテスト用IPと別になるので必ず追加）

## STEP 7. 動作テスト（WPに送らず確認）
```bash
# 単発：1記事を生成してみる（WPへは送らない）
python cli.py --brand RANVOO --category ネッククーラー --no-wp

# バッチ：キューを作って少数で確認
cp data/queue.example.csv data/queue.csv
python batch.py --queue data/queue.csv --limit 1 --no-wp
```
- タイトル等が生成されればOK。次へ。

## STEP 8. cron を設定（無人運用）
サーバーパネル → **「Cron設定」**、またはSSHで `crontab -e`：
```cron
# 記事バッチ：毎朝6時に10件を下書き生成
0 6 * * *   cd ~/afi && .venv/bin/python batch.py --limit 10 >> data/batch.log 2>&1
# IndexNow：30分毎（SEOプラグインで代替するなら不要）
*/30 * * * * cd ~/afi && .venv/bin/python index_submit.py >> data/index.log 2>&1
```
> サーバーパネルのCron設定では「コマンド」に上記の `cd ... && ...` 部分を入れる。

## STEP 9. 確認
```bash
tail -n 50 ~/afi/data/batch.log     # 翌朝、生成ログを確認
```
- WordPressアプリで下書きが増えていればデプロイ成功。

---

## トラブルシューティング
| 症状 | 対処 |
|------|------|
| `pip install` でビルド失敗 | Python3.11をpyenvで使う（wheelが入りやすい）。`pip install --upgrade pip`後に再実行 |
| 楽天APIが400/IPエラー | STEP6のIPが許可リストにあるか。`curl ifconfig.me`の実値で再確認 |
| Gemin  レート制限 | `--limit`を小さく。`GEMINI_MODEL=gemini-2.0-flash`に変更も可 |
| cronが動かない | フルパス指定か確認。`which python3`の絶対パスを使う。実行権限・改行コード |
| プロセスが時間切れ | `--limit`を5前後に下げ、cronを複数時刻に分割 |

## 関連
- [インフラ設計書](./design/infrastructure.md)（#30） / [運用設計書](./design/operations.md)（#31）
- #21 常駐運用・監視 / #22 Runbook
