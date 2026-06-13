# インフラ／デプロイ設計書 — Issue #30

生成ツール（`batch.py` / `index_submit.py`）を「どこで・どう動かすか」。無人・非属人運用の土台。

## 実行環境の選定

### 制約条件
- **固定IPが必要**: 楽天商品検索APIは「許可IP制」。動的IP環境では弾かれる。
- **常時起動が必要**: 無人で毎日バッチを回す。
- **Python＋cronが自由に使える**こと。

### 比較
| 候補 | 固定IP | 常時起動 | Python自由 | コスト | 評価 |
|------|:---:|:---:|:---:|------|------|
| **エックスサーバー同居（採用）** | ◎ | ◎ | ◯(SSH/cron/Python可) | 既存（追加0円） | ✅ 採用。追加コストなし |
| 小型VPS（フォールバック） | ◎ | ◎ | ◎ | 月600〜1000円 | Python制約が出たら移行 |
| 常時起動Mac | △(動的IPが多い) | △(スリープ/電源) | ◎ | 0円 | 安定性に難。非属人に不向き |
| GitHub Actions等 | ✕(動的IP) | スケジュールのみ | ◎ | 無料枠 | 楽天IP許可と相性が悪く不可 |

### 採用構成：エックスサーバー同居（追加コストなし）
WordPressを置いているエックスサーバーは **SSH・cron・Python に対応**しており、
生成ツールを**同じサーバーに同居**できる。固定の共有IPがあるため楽天APIのIP許可も満たせる。
→ 別途VPSは不要。生成ツールはWordPressとREST APIで通信するだけなので、同居しても役割は分離される。

> **フォールバック**: もしエックスサーバー上でPython環境（バージョン/パッケージ導入）に
> 制約が出た場合は、小型VPS（さくら/ConoHa等・月600〜1000円）へ移すだけで同じ構成が動く。

## セットアップ手順（エックスサーバー・初回）
> ⚠️ 実機での実施は #6 で行う。ここでは設計上の手順を示す。

```bash
# 0. サーバーパネルで「SSH設定」を有効化し、公開鍵を登録 → SSHログイン

# 1. Pythonの確認（古い場合はpyenvで3.11等を入れる）
python3 --version          # 3.9未満なら pyenv 推奨（google-genai等が3.9+を要求）
#   例) pyenvでの導入（必要時）:
#   git clone https://github.com/pyenv/pyenv.git ~/.pyenv && ... && pyenv install 3.11.x

# 2. 取得
git clone https://github.com/kgn7645/afi.git && cd afi

# 3. Python環境（プリビルドwheelで導入されるため通常コンパイル不要）
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 4. シークレット
cp .env.example .env
#   GEMINI_API_KEY / WP_* / MOSHIMO_AID / RAKUTEN_APP_ID / RAKUTEN_ACCESS_KEY / INDEXNOW_* を記入
chmod 600 .env

# 5. このサーバーの送信元IPを確認 → 楽天APIの許可IPに登録
curl -s https://api.ipify.org
```

### エックスサーバー特有の注意
- **Pythonバージョン**: 標準python3が古い場合があるため、`pyenv`等で3.11系を用意するのが安全。
- **cron**: サーバーパネルの「Cron設定」からでも、SSHの`crontab -e`からでも設定可。
- **実行時間/プロセス制限**: 共有サーバーは長時間プロセスに制限あり。1回のバッチが長引く場合は
  `--limit` を小さめ（例: 5〜10件×複数回）に分割する。
- **送信元IP**: サイトのIPと送信元IPが同じか、SSHで `curl ifconfig.me` で実値を確認して登録する。

## cron 設定
エックスサーバーのホームは `/home/<アカウント>/`。パスは実環境に合わせる。
```cron
# 記事バッチ：毎朝6時に10件を下書き生成（共有サーバーの実行時間制限に配慮し控えめ）
0 6 * * *   cd ~/afi && .venv/bin/python batch.py --limit 10 >> data/batch.log 2>&1

# IndexNow：30分毎に公開済みを検知して送信（SEOプラグインで代替する場合は不要）
*/30 * * * * cd ~/afi && .venv/bin/python index_submit.py >> data/index.log 2>&1
```

## シークレット・データ管理
| 対象 | 置き場所 | 注意 |
|------|----------|------|
| `.env`（API鍵・WPパスワード） | VPS上のみ。`chmod 600` | **Git禁止**（.gitignore済）。安全な場所にバックアップ |
| キュー | Googleスプレッドシート（#sheet-queue） | 担当者がブラウザ編集。当面はdata/queue.csvでも可 |
| ログ/状態 | `data/*.log` `data/index_state.json` | ローテーション・監視は #21 |
| IndexNowキー | サイトのルート or SEOプラグイン | uploads配下は不可（#17参照） |

## ネットワーク／IP
- 楽天APIの許可IP = **VPSのグローバルIP**（固定）。IP変更時は許可リストも更新。
- WordPress(ouchibase.com)へはHTTPS REST。アプリケーションパスワードで認証。

## レビュー担当（Mac）側に必要なもの
- **Pythonやサーバー操作は一切不要。**
- WordPress公式アプリ（スマホ）/ ブラウザ（Mac）
- キュー編集用のGoogleスプレッドシートへのアクセス権
- WordPress「編集者」アカウント

## 障害・冗長性（→ #21で詳細）
- バッチ失敗時の通知（メール/LINE/Slack）
- ログ監視、再実行手順
- VPS再起動時のcron自動復帰（cronはOS標準で自動起動）

## 関連
- #6 新環境セットアップ手順 / #21 常駐運用・監視 / #sheet-queue キューのシート化
