"""Gemini（Google AI Studio）クライアント。REST直叩き版。

google-genai SDK のバージョン差（Gemini 3 / thinking_config 未対応など）に左右されない
よう、generateContent REST API を直接呼ぶ。複数APIキー対応：プライマリが枠切れ(429)に
なったら次のキーへ自動フォールバック。課金キーを GEMINI_API_KEY(=プライマリ) に置く。

思考(thinking)制御:
- Gemini 3 系は既定で思考が有効＝出力課金が増える。`thinking_budget=0` で思考を止められる
  （タイトル/信頼度/カテゴリ選択など軽いコールはOFF、本文など品質が要る所はON維持）。

各生成の実トークン（usageMetadata）を data/gemini_usage.csv に記録し概算コストも算出する。"""
from __future__ import annotations

import csv
import json
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

from .config import ROOT, get_settings

# 無料枠のRPM(1分あたりリクエスト)上限に当たらないための最小間隔(秒)。
# 予備キーが無料枠の場合に備えて維持（課金プライマリのみなら実害は軽微）。
_MIN_INTERVAL = 4.5

# 概算単価（USD / 100万トークン）。出力は可視+思考の合計に適用。
# ※公式 https://ai.google.dev/gemini-api/docs/pricing で要更新（2026-06取得値）。
_RATES = {
    "gemini-2.5-flash-lite": (0.10, 0.40),
    "gemini-2.5-flash": (0.30, 2.50),
    "gemini-2.5-pro": (1.25, 10.00),
    "gemini-2.0-flash": (0.10, 0.40),
    "gemini-3.1-flash-lite": (0.25, 1.50),
    "gemini-3-flash-preview": (0.50, 3.00),
    "gemini-3.5-flash": (1.50, 9.00),
    "gemini-3-pro-preview": (2.00, 12.00),
}
_USAGE_CSV = ROOT / "data" / "gemini_usage.csv"
_USAGE_FIELDS = ["datetime", "model", "prompt", "candidates", "thoughts",
                 "total", "est_cost_usd"]

# 設定画面のドロップダウン用（id, ラベル）。実測コストは思考最適化・課金単価ベースの目安。
MODEL_CHOICES = [
    ("gemini-3.1-flash-lite", "Gemini 3.1 Flash-Lite（推奨・最安 約¥0.8/記事・思考なし・高速）"),
    ("gemini-3-flash-preview", "Gemini 3 Flash preview（約¥5/記事・preview）"),
    ("gemini-3.5-flash", "Gemini 3.5 Flash（高品質・思考あり 約¥12/記事）"),
    ("gemini-2.5-flash", "Gemini 2.5 Flash（約¥4/記事）"),
    ("gemini-2.5-flash-lite", "Gemini 2.5 Flash-Lite（旧・最安だが品質低）"),
]


def resolve_model() -> str:
    """使用モデルを解決: 設定(overrides/config.yaml gemini.model) > env GEMINI_MODEL > 既定。

    設定画面のドロップダウンで切替→WPオーバーライド経由で両ホスト(Xserver/Render)即反映。
    """
    try:
        from .config import get_rules
        m = ((get_rules().get("gemini") or {}).get("model") or "").strip()
        if m:
            return m
    except Exception:  # noqa: BLE001
        pass
    return get_settings().gemini_model


def record_shared_usage(summary: dict) -> None:
    """1プロセス分の累計usage(usage_summary)を共有overridesの`_gemini_usage`に加算。

    Xserver(生成)とRender(リライト)の消費を1か所に合算し、AI設定画面で「概算消費/残」を表示する。
    概算（自前のトークン集計×単価）であり、実請求はGoogle Cloud billingが正。失敗しても無視。
    """
    if not summary or not summary.get("calls"):
        return
    try:
        from datetime import datetime

        from . import overrides
        if not overrides.enabled():
            return
        cur = dict(overrides.load(force=True).get("_gemini_usage") or {})
        cur["calls"] = int(cur.get("calls", 0)) + int(summary.get("calls", 0))
        cur["tokens"] = int(cur.get("tokens", 0)) + int(summary.get("total", 0))
        cur["cost_usd"] = round(float(cur.get("cost_usd", 0.0))
                                + float(summary.get("est_cost_usd", 0.0)), 6)
        day = datetime.now().astimezone().strftime("%Y-%m-%d")
        byday = dict(cur.get("by_day") or {})
        byday[day] = round(float(byday.get(day, 0.0))
                           + float(summary.get("est_cost_usd", 0.0)), 6)
        cur["by_day"] = dict(sorted(byday.items())[-30:])  # 直近30日のみ保持
        cur["updated"] = day
        overrides.update({"_gemini_usage": cur})
    except Exception:  # noqa: BLE001
        pass
_ENDPOINT = ("https://generativelanguage.googleapis.com/v1beta/"
             "models/{model}:generateContent?key={key}")


def _rates_for(model: str) -> tuple[float, float]:
    return _RATES.get(model, _RATES["gemini-2.5-flash"])  # 不明なら flash 単価


class GeminiClient:
    def __init__(self) -> None:
        self.settings = get_settings()
        self._model = resolve_model()  # 設定画面で切替可（overrides>env>既定）
        self._keys = list(self.settings.gemini_api_keys)
        self._ki = 0  # 現在使用中のキーindex（429で前進し戻らない）
        self._last_call = 0.0
        # 実トークン累計（このインスタンスの生存期間＝1バッチ等で合算）
        self.usage = {"calls": 0, "prompt": 0, "candidates": 0,
                      "thoughts": 0, "total": 0, "est_cost_usd": 0.0}

    def _throttle(self) -> None:
        elapsed = time.time() - self._last_call
        if elapsed < _MIN_INTERVAL:
            time.sleep(_MIN_INTERVAL - elapsed)
        self._last_call = time.time()

    def _post(self, key: str, body: dict) -> dict:
        url = _ENDPOINT.format(model=self._model, key=key)
        req = urllib.request.Request(
            url, data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=180) as r:
            return json.load(r)

    def _record_usage(self, um: dict) -> None:
        """usageMetadata(dict) を累計＋CSVへ追記（失敗しても生成は止めない）。"""
        if not um:
            return
        p = int(um.get("promptTokenCount", 0) or 0)
        c = int(um.get("candidatesTokenCount", 0) or 0)
        th = int(um.get("thoughtsTokenCount", 0) or 0)
        t = int(um.get("totalTokenCount", 0) or (p + c + th))
        in_rate, out_rate = _rates_for(self._model)
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
                    "model": self._model,
                    "prompt": p, "candidates": c, "thoughts": th, "total": t,
                    "est_cost_usd": round(cost, 6)})
        except Exception:  # noqa: BLE001
            pass

    @staticmethod
    def _extract_text(resp: dict) -> str:
        """candidates から可視テキストを連結（thought パートは除外）。"""
        cands = resp.get("candidates") or []
        if not cands:
            return ""
        parts = (cands[0].get("content") or {}).get("parts") or []
        return "".join(p.get("text", "") for p in parts if not p.get("thought")).strip()

    @staticmethod
    def _is_quota(code: int, body: str) -> bool:
        return code == 429 or "RESOURCE_EXHAUSTED" in body

    def _build_body(self, prompt: str, temperature: float,
                    thinking_budget: int | None, grounded: bool) -> dict:
        gen_cfg: dict = {"temperature": temperature}
        if thinking_budget is not None:
            gen_cfg["thinkingConfig"] = {"thinkingBudget": thinking_budget}
        body: dict = {"contents": [{"parts": [{"text": prompt}]}],
                      "generationConfig": gen_cfg}
        if grounded:
            body["tools"] = [{"google_search": {}}]
        return body

    def _generate(self, prompt: str, *, temperature: float,
                  thinking_budget: int | None = None, grounded: bool = False) -> str:
        """キーをローテーションしながら生成。各キーで429なら次キーへ。"""
        if not self._keys:
            raise RuntimeError("GEMINI_API_KEY が未設定です。.env に設定してください。")
        body = self._build_body(prompt, temperature, thinking_budget, grounded)
        last_err: object = None
        while self._ki < len(self._keys):
            for attempt in range(3):
                self._throttle()
                try:
                    resp = self._post(self._keys[self._ki], body)
                    self._record_usage(resp.get("usageMetadata") or {})
                    return self._extract_text(resp)
                except urllib.error.HTTPError as e:  # noqa: PERF203
                    try:
                        msg = e.read().decode("utf-8", "ignore")[:400]
                    except Exception:  # noqa: BLE001
                        msg = ""
                    last_err = f"HTTP{e.code}: {msg}"
                    if self._is_quota(e.code, msg):
                        break  # このキーは枠切れ → 次のキーへ
                    if attempt < 2:
                        time.sleep(10 * (attempt + 1))  # 一時エラーは指数バックオフ
                except Exception as e:  # noqa: BLE001
                    last_err = e
                    if attempt < 2:
                        time.sleep(10 * (attempt + 1))
            self._ki += 1  # 次のキーへ前進
        n = len(self._keys)
        raise RuntimeError(f"Gemini生成に失敗しました（{n}キーすべて枠切れ/失敗）: {last_err}")

    def generate(self, prompt: str, *, temperature: float = 0.8,
                 thinking_budget: int | None = None, max_retries: int = 3) -> str:
        return self._generate(prompt, temperature=temperature,
                              thinking_budget=thinking_budget)

    def generate_grounded(self, prompt: str, *, temperature: float = 0.4,
                          thinking_budget: int | None = None, max_retries: int = 2) -> str:
        """Google検索グラウンディング付き生成（Issue #15）。失敗時は通常生成にフォールバック。"""
        try:
            return self._generate(prompt, temperature=temperature,
                                  thinking_budget=thinking_budget, grounded=True)
        except Exception:  # noqa: BLE001
            return self.generate(prompt, temperature=temperature,
                                thinking_budget=thinking_budget)

    def usage_summary(self) -> dict:
        """このインスタンスの累計トークン＋概算コスト。"""
        u = dict(self.usage)
        u["est_cost_usd"] = round(u["est_cost_usd"], 4)
        u["est_cost_jpy"] = round(u["est_cost_usd"] * 150, 1)
        return u
