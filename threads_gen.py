"""Claude Codeモードの生成ツール（Gemini API不使用）。

文章生成だけを Claude Code（の中の人）が担う運用。商品・プロンプト・設定はアプリ側のまま。

手順:
  1) python3 threads_gen.py dump
       → 生成待ちのプロンプトを threads_gen_pending.json に書き出す
  2) Claude Code が threads_gen_pending.json を読み、各 id の文章を作って results.json に保存
       PR:      {"<id>": {"clean_name":"簡潔な商品名", "captions":["案1",...5], "reply":"リプ"}}
       つぶやき: {"<id>": {"caption":"本文"}}
  3) python3 threads_gen.py apply results.json
       → 投稿タブのドラフトに反映（PRは画像も自動付与・@cosme/LIPS公式画像があればホスト）
"""
from __future__ import annotations

import json
import sys

from core import threads_pipeline as tp

PENDING = "threads_gen_pending.json"


def dump() -> None:
    pend = tp.pending_generation()
    with open(PENDING, "w", encoding="utf-8") as f:
        json.dump(pend, f, ensure_ascii=False, indent=2)
    print(f"生成待ち {len(pend)}件 → {PENDING}")
    for x in pend:
        print(f"  [{x['type']}] {x['id']}  {x['product'][:34]}")
    if pend:
        print("\n→ Claude Codeで各プロンプトに沿って文章を作り results.json に保存後、apply してください。")


def apply(path: str) -> None:
    with open(path, encoding="utf-8") as f:
        results = json.load(f)
    made = tp.apply_generation(results)
    print(f"取り込み {made}件 → 投稿タブ（商品PR/つぶやき）へ反映しました。")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "dump"
    if cmd == "dump":
        dump()
    elif cmd == "apply" and len(sys.argv) > 2:
        apply(sys.argv[2])
    else:
        print("usage: python3 threads_gen.py dump | apply <results.json>")
