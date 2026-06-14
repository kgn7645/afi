#!/usr/bin/env bash
# =====================================================================
# エックスサーバー用 セットアップスクリプト（Issue #6）
# 事前ビルド版 Python 3.11 を導入し、venv＋依存をインストールする。
# （標準Pythonが3.6.8と古く、pyenvのコンパイルは共有サーバーで失敗しやすいため、
#   コンパイル不要の python-build-standalone を使う）
#
# 使い方:  cd ~/afi && bash scripts/setup_xserver.sh
# =====================================================================
set -euo pipefail

PY_URL="https://github.com/astral-sh/python-build-standalone/releases/download/20260610/cpython-3.11.15%2B20260610-x86_64-unknown-linux-gnu-install_only.tar.gz"
PY_DIR="$HOME/opt/python"
PY_BIN="$PY_DIR/bin/python3"
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

echo "==> [1/4] 事前ビルド版 Python 3.11 を準備"
if [ -x "$PY_BIN" ]; then
  echo "    既に導入済み: $("$PY_BIN" -V)"
else
  mkdir -p "$HOME/opt"
  echo "    ダウンロード中..."
  curl -fsSL "$PY_URL" -o /tmp/py311.tgz
  tar xzf /tmp/py311.tgz -C "$HOME/opt"   # 展開で $HOME/opt/python が作られる
  rm -f /tmp/py311.tgz
  echo "    導入完了: $("$PY_BIN" -V)"
fi

echo "==> [2/4] 必須モジュール確認 (ssl/ctypes/sqlite3 など)"
"$PY_BIN" -c "import ssl,ctypes,sqlite3,lzma,bz2; print('    modules OK:', ssl.OPENSSL_VERSION)"

echo "==> [3/4] venv 作成（$REPO_ROOT/.venv）"
cd "$REPO_ROOT"
"$PY_BIN" -m venv .venv
./.venv/bin/pip install --upgrade pip >/dev/null

echo "==> [4/4] 依存インストール"
./.venv/bin/pip install -r requirements.txt

echo ""
echo "✅ セットアップ完了。次の手順:"
echo "  1) cp .env.example .env && nano .env   # 各APIキー等を記入"
echo "     chmod 600 .env"
echo "  2) 楽天APIの『許可IP』に  $(curl -s https://api.ipify.org)  を追加"
echo "  3) 動作テスト:  ./.venv/bin/python cli.py --brand RANVOO --category ネッククーラー --no-wp"
echo "  4) cron 設定（docs/deploy-xserver.md の STEP 8 参照）"
