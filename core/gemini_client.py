"""Gemini（Google AI Studio 無料枠）クライアント。
複数APIキー対応：プライマリが枠切れ(429)になったら次のキーへ自動フォールバック。

各生成の実トークン（usage_metadata）を data/gemini_usage.csv に記録し、概算コストも
算出する（docs/gemini-pricing-simulation.md を実測で更新するため）。"""
from __future__ import annotations

import csv
import time
from datetime import datetime, timezone

from .config import ROOT, get_settings

# 無料枠のRPM(1分あたりリクエスト)上限に当たらないための最小間隔(秒)
_MIN_INTERVAL = 4.5

# 概算単価（USD / 100万トークン）。出力は可視+思考の合計に適用。
# ※2026年初時点の概算。公式 https://ai.google.dev/gemini-api/docs/pricing で要更新。
_RATES = {
    "gemini-2.5-flash-lite": (0.10, 0.40),
    "gemini-2.5-flash": (0.30, 2.50),
    "gemini-2.5-pro": (1.25, 10.00),
    "gemini-2.0-flash": (0.10, 0.40),
}
_USAGE_CSV = ROOT / "data" / "gemini_usage.csv"
_USAGE_FIELDS = ["datetime", "model", "prompt", "candidates", "thoughts",
                 "total", "est_cost_usd"]


def _rates_for(model: str) -> tuple[float, float]:
    return _RATES.get(model, _RATES["gemini-2.5-flash"])  # 不明なら flash 単価


def _is_quota(e: Exception) -> bool:
    m = str(e)
    return "429" in m or "RESOURCE_EXHAUSTED" in m


class GeminiClient:
    def __init__(self) -> None:
        self.settings = get_settings()
        self._keys = list(self.settings.gemini_api_keys)
        self._ki = 0  # 現在使用中のキーindex（429で前進し戻らない）
        self._clients: dict[str, object] = {}
        self._last_call = 0.0
        # 実トークン累計（このインスタンスの生存期間＝1バッチ等で合算）
        self.usage = {"calls": 0, "prompt": 0, "candidates": 0,
                      "thoughts": 0, "total": 0, "est_cost_usd": 0.0}

    def _client(self, key: str):
        if key not in self._clients:
            from google import genai  # 遅延import

            self._clients[key] = genai.Client(api_key=key)
        return self._clients[key]

    def _throttle(self) -> None:
        elapsed = time.time() - self._last_call
        if elapsed < _MIN_INTERVAL:
            time.sleep(_MIN_INTERVAL - elapsed)
        self._last_call = time.time()

    def _record_usage(self, resp) -> None:
        """resp.usage_metadata を累計＋CSVへ追記（失敗しても生成は止めない）。"""
        um = getattr(resp, "usage_metadata", None)
        if not um:
            return
        p = int(getattr(um, "prompt_token_count", 0) or 0)
        c = int(getattr(um, "candidates_token_count", 0) or 0)
        th = int(getattr(um, "thoughts_token_count", 0) or 0)
        t = int(getattr(um, "total_token_count", 0) or (p + c + th))
        in_rate, out_rate = _rates_for(self.settings.gemini_model)
        cost = p * in_rate / 1e6 + (c + th) * out_rate / 1e6
        self.usage["calls"] += 1
        self.usage["prompt"] += p
        self.usage["candidates"] += c
        self.usage["thoughts"] += th
        self.usage["total"] += t
        self.usage["est_cost_usd"] += cost
        try:
            _USAGE_CSV.parent.mkdir(parents=True, exist_ok=True)
            new = not _USAGE_CSV.exists()
            with _USAGE_CSV.open("a", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=_USAGE_FIELDS)
                if new:
                    w.writeheader()
                w.writerow({
                    "datetime": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
                    "model": self.settings.gemini_model,
                    "prompt": p, "candidates": c, "thoughts": th, "total": t,
                    "est_cost_usd": round(cost, 6)})
        except Exception:  # noqa: BLE001
            pass

    def usage_summary(self) -> dict:
        """このインスタンスの累計トークン＋概算コスト。"""
        u = dict(self.usage)
        u["est_cost_usd"] = round(u["est_cost_usd"], 4)
        u["est_cost_jpy"] = round(u["est_cost_usd"] * 150, 1)
        return u

    def _generate(self, prompt: str, config) -> str:
        """キーをローテーションしながら生成。各キーで429なら次キーへ。"""
        if not self._keys:
            raise RuntimeError("GEMINI_API_KEY が未設定です。.env に設定してください。")
        last_err: Exception | None = None
        while self._ki < len(self._keys):
            client = self._client(self._keys[self._ki])
            for attempt in range(3):
                self._throttle()
                try:
                    resp = client.models.generate_content(
                        model=self.settings.gemini_model, contents=prompt, config=config)
                    self._record_usage(resp)
                    return (resp.text or "").strip()
                except Exception as e:  # noqa: BLE001
                    last_err = e
                    if _is_quota(e):
                        break  # このキーは枠切れ → 次のキーへ
                    if attempt < 2:
                        time.sleep(10 * (attempt + 1))  # 一時エラーは指数バックオフ
            self._ki += 1  # 次のキーへ前進
        n = len(self._keys)
        raise RuntimeError(f"Gemini生成に失敗しました（{n}キーすべて枠切れ/失敗）: {last_err}")

    def generate(self, prompt: str, *, temperature: float = 0.8, max_retries: int = 3) -> str:
        return self._generate(prompt, {"temperature": temperature})

    def generate_grounded(self, prompt: str, *, temperature: float = 0.4,
                          max_retries: int = 2) -> str:
        """Google検索グラウンディング付き生成（Issue #15）。

        グラウンディング不可（SDK/モデル非対応・全キー枠切れ等）は通常生成にフォールバック。
        """
        try:
            from google.genai import types

            tool = types.Tool(google_search=types.GoogleSearch())
            config = types.GenerateContentConfig(temperature=temperature, tools=[tool])
            return self._generate(prompt, config)
        except Exception:  # noqa: BLE001
            return self.generate(prompt, temperature=temperature)
