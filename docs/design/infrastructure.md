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
| **小型VPS（推奨）** | ◎ | ◎ | ◎ | 月600〜1000円 | ✅ 最適。非属人・無人に向く |
| 既存サーバー | △(要確認) | ◎ | △(レンタルだと不可な場合) | 既存 | 条件を満たせば可 |
| 常時起動Mac | △(動的IPが多い) | △(スリープ/電源) | ◎ | 0円 | 安定性に難。非属人に不向き |
| GitHub Actions等 | ✕(動的IP) | スケジュールのみ | ◎ | 無料枠 | 楽天IP許可と相性が悪く不可 |

### 推奨構成
- **小型VPS**（さくらのVPS / ConoHa / Xserver VPS など）
  - スペック目安: 1vCPU / メモリ1GB / SSD（最小プランで十分）
  - OS: Ubuntu LTS
  - 固定グローバルIPを取得 → **楽天APIの許可IPに登録**

## セットアップ手順（VPS・初回）
```bash
# 1. 依存
sudo apt update && sudo apt install -y python3 python3-venv git

# 2. 取得
git clone https://github.com/kgn7645/afi.git
cd afi

# 3. Python環境
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 4. シークレット
cp .env.example .env
#   GEMINI_API_KEY / WP_* / MOSHIMO_AID / RAKUTEN_APP_ID / RAKUTEN_ACCESS_KEY / INDEXNOW_* を記入
chmod 600 .env

# 5. 楽天APIの許可IPに、このVPSのグローバルIPを登録（楽天ウェブサービス管理画面）
curl -s https://api.ipify.org   # ← このIPを許可リストへ
```
> 詳細な新環境セットアップは #6 を参照。

## cron 設定
```cron
# 記事バッチ：毎朝6時に15件を下書き生成
0 6 * * *   cd /home/USER/afi && .venv/bin/python batch.py --limit 15 >> data/batch.log 2>&1

# IndexNow：30分毎に公開済みを検知して送信（SEOプラグインで代替する場合は不要）
*/30 * * * * cd /home/USER/afi && .venv/bin/python index_submit.py >> data/index.log 2>&1
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
