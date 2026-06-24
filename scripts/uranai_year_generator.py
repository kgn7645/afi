"""superuniverseoracle 用・1年分(365日×3)占い投稿ジェネレータ。

方針: 全投稿が占い/スピ系・本文にリンクなし（リンクはbioのみ）。
ペルソナ=宇宙/オラクル系・優しい断定(reikan風)。絵文字アクションで参加を促す。
※同一文面の反復はMetaにスパム判定されるため、フレーズプール×日付シードで全件ユニーク化。

1日3枠:
  08:00  morning  = 今日の運勢（12星座 / 誕生月 / ラッキー星座ランキング を日替わり）
  12:00  message  = 宇宙からのメッセージ（絵文字置かせオラクル / オラクル1枚引き）
  20:00  night    = 夜の開運（開運アクション / 誕生日金運 / 絵文字置かせ）

出力: data/superuniverseoracle_year.json  [{date, time, slot, style, text}, ...]
使い方: python3 scripts/uranai_year_generator.py [開始日YYYY-MM-DD] [日数]
"""
from __future__ import annotations
import json, random, sys
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "superuniverseoracle_year.json"

SIGNS = [
    ("♈", "おひつじ座"), ("♉", "おうし座"), ("♊", "ふたご座"), ("♋", "かに座"),
    ("♌", "しし座"), ("♍", "おとめ座"), ("♎", "てんびん座"), ("♏", "さそり座"),
    ("♐", "いて座"), ("♑", "やぎ座"), ("♒", "みずがめ座"), ("♓", "うお座"),
]
# エレメント（0=火,1=地,2=風,3=水）を Aries..Pisces 順に
ELEMENTS = [0, 1, 2, 3, 0, 1, 2, 3, 0, 1, 2, 3]
ELEM_NAME = {0: "火", 1: "地", 2: "風", 3: "水"}


def moon_sign_index(d: date) -> int:
    """その日(JST正午)の月の星座 index(0=おひつじ..11=うお)。月の平均黄経の近似。
    月は約27.3日で黄道一周＝1星座約2.3日。外部データ不要で決定的。"""
    jd_noon = d.toordinal() + 1721425.0          # JST正午相当のユリウス日
    dd = jd_noon - 2451545.0                      # J2000.0 からの経過日
    lon = (218.316 + 13.176396 * dd) % 360.0      # 月の平均黄経(度)
    return int(lon // 30) % 12


def _compat(sign: int, moon: int) -> float:
    """月の星座と各星座の相性スコア（高いほど吉）。アスペクト距離(0〜11)で評価。"""
    diff = (sign - moon) % 12
    return {0: 5.0,            # 同じ星座（月が宿る）
            4: 4.2, 8: 4.2,    # トライン120°（同エレメント）
            2: 3.4, 10: 3.4,   # セクスタイル60°（協力エレメント）
            6: 2.8,            # オポジション180°（引き合う）
            1: 2.2, 11: 2.2,   # セミセクスタイル30°
            5: 1.6, 7: 1.6,    # クインカンクス150°
            3: 1.4, 9: 1.4,    # スクエア90°（試練）
            }.get(diff, 2.0)


def daily_rank(d: date, seed: random.Random) -> tuple[list[int], int]:
    """その日の月の星座を基に上位5星座を算出。(top5 index, 月の星座index)。
    相性スコア＋微小な日替わりノイズで、同月星座の連日でも順位が自然に動く。"""
    moon = moon_sign_index(d)
    # 月の星座×相性で「上位に来やすさ」を決めつつ、日替わりの揺らぎを大きめ(2.6)に取り、
    # 毎日 顔ぶれ・順位が動くように。月のエレメント星座は傾向として上位に出やすい。
    def score(s: int) -> float:
        boost = 0.8 if s == moon else 0.0
        return _compat(s, moon) + boost + seed.random() * 2.6
    scored = sorted(range(12), key=lambda s: -score(s))
    return scored[:5], moon
EMOJI = ["🌙", "✨", "🌹", "🕊️", "💫", "💰", "🔮", "🌕", "🐱", "🌈", "⭐", "🪽"]

# 金運軸のラッキー要素・締め（MDの金運開運法ベース。断定/誇大・必ず/絶対は使わない）
LUCKY_COLORS = ["ゴールド", "山吹色", "若草色", "ラベンダー", "パールホワイト", "深緑", "ターコイズ", "小豆色"]
LUCKY_DIRS = ["東", "南東", "南", "北西", "西"]
# ランキングの締め（金運軸）。{e}=絵文字 {color}=ラッキーカラー {dir}=方位
RANK_TAIL = [
    "上位の星座さん、今日はお財布の中を整えると金運の流れがもっと整います💰 共感したら{e}を置いて",
    "ランクインした人へ。お札の向きをそろえるだけで、巡りが変わる合図です✨",
    "1位さんは思いがけない豊かさの波が近づいているかも💰 受け取る人は{e}を置いて",
    "今日のラッキーカラーは{color}。財布や小物に取り入れると金運が動きやすい日✨",
    "上位の方は{dir}の窓を開けて新しい気を。お金とご縁が巡りやすくなります🪙 ピンと来たら{e}を",
    "玄関にお花や緑をひとつ飾ってみて。良いご縁とお金が入ってくる入口になります🌿",
    "ランクインした人、抱えていた重さがふっと軽くなる日✨ そっと{e}を置いて受け取って",
    "上位さんへ。新しい財布を迎えるなら今日が好機。種銭を入れて始めると流れに乗れます💰",
    "今日は{dir}まわりを少し片づけると金運アップの合図。できた人は{e}を置いて",
    "1〜3位さんは、今日の小さな「ありがとう」が豊かさの種に。{color}を身につけると後押しに✨",
]

# ---------- A: 誕生月の運勢 ----------
MONTH_THEME = {
    "金運": ["お金の流れが大きく変わろうとしています", "豊かさの扉が静かに開き始めています",
            "臨時収入の波があなたに向かっています", "ずっと止まっていた金運が動き出します"],
    "恋愛": ["止まっていたご縁が、再び動き始めます", "運命の人との距離が縮まる時です",
            "心に引っかかるあの人から、流れが変わります", "新しい出会いの星が巡ってきています"],
    "転機": ["人生の大きな節目が近づいています", "古い自分を手放すと、道がひらけます",
            "宇宙があなたを次のステージへ押し上げます", "迷っていた選択に、答えが降りてきます"],
    "癒し": ["頑張りすぎた心が、ふっと軽くなります", "あなたを縛っていたものが解けていきます",
            "立ち止まっていい、と宇宙が言っています", "心の重荷が、これからほどけていきます"],
}
MONTH_CLOSE = [
    "受け取る準備ができた合図です", "もう、変わり始めています", "この流れに身をまかせて大丈夫",
    "あなたは、ちゃんと導かれています", "怖がらなくていい、ただ受け取って",
]

# ---------- B: 12星座 今日の運勢（単星座フィーチャー） ----------
SIGN_WORDS = ["流れに乗る日", "受け取る日", "手放す日", "整える日", "踏み出す日",
              "休む勇気の日", "直感が冴える日", "ご縁が動く日", "言葉が力になる日", "立ち止まる日"]
SIGN_BODY = [
    "宇宙からのギフトは、あなたが思うより近くにあります", "空を見上げた瞬間が、今日のサインです",
    "焦らなくていい。タイミングは、もう動き始めています", "心がざわつくなら、それは扉が開く前ぶれ",
    "今日のあなたの選択は、未来の自分への贈り物になります", "無理に進めなくていい。流れが運んでくれます",
    "迷ったら、心が軽くなる方を選んで", "今日は、あなたの直感がいちばんの占い師です",
]

# ---------- C: 絵文字置かせオラクル ----------
C_HOOK = [
    "このメッセージが目に留まったあなたへ", "今これを見たということは、偶然ではありません",
    "宇宙が、いまあなたを呼んでいます", "スクロールの手が止まったあなたへ",
    "このタイミングで届いたのには、意味があります", "ふと気になって開いたあなたへ",
]
C_PROMISE = [
    "ここから流れが変わり始めます", "良いことしか、起こりません",
    "止まっていた歯車が、静かに動き出します", "あなたの願いは、もう動いています",
    "受け取る準備ができた人から、叶っていきます", "宇宙が、そっと味方についています",
]
C_COND = ["心が動いたなら", "そう感じたなら", "ピンと来たなら", "迷わず"]

# ---------- D: オラクル1枚引き ----------
CARDS = {
    "手放し": "握りしめていたものを離すと、もっと良いものが入ってきます",
    "再生": "終わりに見えたものは、新しい始まりの入口です",
    "豊かさ": "あなたはもう、受け取るに値する存在です",
    "直感": "理由はいらない。心が選んだ方が、正解です",
    "休息": "立ち止まることも、前へ進むことのひとつです",
    "出会い": "あなたを変える誰かが、もうすぐ現れます",
    "転機": "扉はすでに開いています。あとは、くぐるだけ",
    "浄化": "古い感情を流すと、心に空き地が生まれます",
    "感謝": "ありがとうを数えるほど、宇宙はあなたに味方します",
    "勇気": "怖さは、あなたが本気で望んでいる証拠です",
    "光": "暗いと思った場所こそ、いちばん星がよく見えます",
    "ご縁": "離れたように見えた糸が、また結ばれていきます",
}

# ---------- E: 夜の開運アクション ----------
E_ITEMS = [
    ("今夜は満月🌕", "お財布を月の光にそっとかざしてみて。金運の流れが整います"),
    ("新月の夜🌑", "叶えたい願いを、3つだけ紙に書いて。宇宙に届きます"),
    ("夜、眠る前に", "今日あった小さな「よかったこと」を、ひとつ思い出して。豊かさの種になります"),
    ("一日の終わりに", "鏡の中の自分に「おつかれさま」と言ってあげて。明日の運気が上がります"),
    ("寝る前のひと呼吸", "窓を少し開けて深呼吸を。滞っていた気が入れ替わります"),
    ("今夜のおまじない", "枕元にコップ一杯の水を。悪い夢と不要なものを吸い取ってくれます"),
    ("月が見える夜は", "月に向かって「ありがとう」を。願いが叶うスピードが上がります"),
    ("夜の浄化タイム", "塩をひとつまみ手のひらに。今日もらった重さを、そっと洗い流して"),
    ("おやすみ前に", "好きな香りを枕元に。心が整うと、いい流れが舞い込みます"),
    ("今夜の合図", "玄関を軽く拭いてみて。良いご縁とお金は、きれいな入口から入ってきます"),
    ("眠る前のひとこと", "「明日もきっと大丈夫」と口にして。言葉が現実をつくります"),
    ("夜空を見上げたら", "いちばん光る星に願いを預けて。あなたの願いは、もう届いています"),
    ("寝る前のリセット", "スマホを少し早く手放して。静けさが、運気の通り道をつくります"),
    ("今夜のおすそわけ", "誰かひとりの幸せを願ってみて。巡り巡って、あなたに返ってきます"),
]
E_CLOSE = [
    "やってみた人は{e}を置いて教えてね", "今夜やる人は{e}を置いて",
    "受け取れた人は、そっと{e}を", "できたら{e}を置いて宣言しよう", "やる人だけ{e}を置いて",
]

# ---------- 誕生月 金運（A派生・夜用） ----------
def birth_money(seed: random.Random, m: int) -> str:
    e = seed.choice(EMOJI)
    body = seed.choice([
        f"宇宙が今、あなたの「お金の扉」を開けようとしています",
        f"近いうちに、思いがけない形で豊かさが巡ってきます",
        f"ずっと我慢してきたあなたに、ご褒美の流れが来ています",
        f"金運の波が、もうすぐあなたのところへ届きます",
    ])
    return f"【{m}月生まれのあなたへ】{body}💰 もうすぐ流れが変わる人、ここに{e}を置いて。{seed.choice(MONTH_CLOSE)}"


def style_A_month(seed: random.Random, m: int) -> str:
    theme = seed.choice(list(MONTH_THEME))
    e = seed.choice(EMOJI)
    return (f"【{m}月生まれのあなたへ】{seed.choice(MONTH_THEME[theme])}{e} "
            f"{seed.choice(MONTH_CLOSE)}。そっと{seed.choice(EMOJI)}を置いて、受け取る合図に")


def style_B_sign(seed: random.Random, idx: int) -> str:
    em, name = SIGNS[idx % 12]
    return (f"{em}{name}｜今日は「{seed.choice(SIGN_WORDS)}」。{seed.choice(SIGN_BODY)}{seed.choice(EMOJI)}")


def style_B_rank(seed: random.Random, d: date) -> str:
    top5, moon = daily_rank(d, seed)
    e = seed.choice(EMOJI)
    lines = "\n".join(f"{i+1}位 {SIGNS[o][0]}{SIGNS[o][1]}" for i, o in enumerate(top5))
    tail = seed.choice(RANK_TAIL).format(
        e=e, color=seed.choice(LUCKY_COLORS), dir=seed.choice(LUCKY_DIRS))
    moon_name = SIGNS[moon][1]
    elem = ELEM_NAME[ELEMENTS[moon]]
    # 算出根拠（月の星座×エレメント）を冒頭に出して説得力を持たせる
    lead = seed.choice([
        f"今日は月が{moon_name}に。{elem}のエネルギーが満ちる一日🌙",
        f"本日の月は{moon_name}。{elem}の星座に追い風が巡ります🌙",
        f"月が{moon_name}を運行中。{elem}のリズムが今日の鍵🌙",
        f"今日の月は{moon_name}。{elem}に縁のある星座が上位に🌙",
    ])
    return f"🔮今日の運勢ランキング🔮\n{lead}\n{lines}\n{tail}"


def style_C(seed: random.Random) -> str:
    e = seed.choice(EMOJI)
    return f"{seed.choice(C_HOOK)}。{seed.choice(C_COND)}{e}を置いて。{seed.choice(C_PROMISE)}"


D_OPEN = [
    "🔮今日、宇宙から引いたカードは", "🌙今朝あなたに届いた宇宙のカードは",
    "✨今日のオラクルメッセージは", "🪽今のあなたに必要なカードは", "🔮宇宙が今日選んだカードは",
]
D_CLOSE = [
    "心当たりがある人は{e}を置いて", "受け取れた人は、そっと{e}を",
    "ピンと来たら{e}を置いてね", "今日の自分に{e}を贈ってあげて", "わかる人だけ{e}を置いて",
]

def style_D(seed: random.Random) -> str:
    card = seed.choice(list(CARDS))
    e = seed.choice(EMOJI)
    return f"{seed.choice(D_OPEN)}『{card}』。{CARDS[card]}。{seed.choice(D_CLOSE).format(e=e)}"


def style_E(seed: random.Random) -> str:
    occ, act = seed.choice(E_ITEMS)
    e = seed.choice(EMOJI)
    return f"【{occ}】{act}。{seed.choice(E_CLOSE).format(e=e)}"


def gen_day(d: date, seen: set) -> list[dict]:
    """その日の3投稿。各枠のスタイルを決め、既出文面なら別シードで振り直して全件ユニーク化。"""
    doy = d.timetuple().tm_yday
    base = d.toordinal()

    def emit(style: str, thunk) -> str:
        # thunk(Random)->str。既出ならシードをずらして再抽選し全件ユニーク化
        for k in range(600):
            text = thunk(random.Random(base * 9973 + k * 31))
            if text not in seen:
                seen.add(text)
                return text
        seen.add(text)
        return text

    # 朝は反応の良い「運勢ランキング（金運軸）」を主軸に。
    # 5日に1回だけ誕生月メッセージを挟んで単調さを避ける。
    morning = ("A_month", lambda r: style_A_month(r, ((doy // 3) % 12) + 1)) \
        if doy % 5 == 0 else ("B_rank", lambda r: style_B_rank(r, d))
    message = [
        ("C_emoji", style_C),
        ("D_oracle", style_D),
    ][doy % 2]
    night = [
        ("E_action", style_E),
        ("A_money", lambda r: birth_money(r, ((doy // 3) % 12) + 1)),
        ("C_emoji", style_C),
    ][doy % 3]

    return [
        {"time": "08:00", "slot": "morning", "style": morning[0], "text": emit(*morning)},
        {"time": "12:00", "slot": "message", "style": message[0], "text": emit(*message)},
        {"time": "20:00", "slot": "night", "style": night[0], "text": emit(*night)},
    ]


def main():
    start = datetime.strptime(sys.argv[1], "%Y-%m-%d").date() if len(sys.argv) > 1 else date(2026, 6, 21)
    days = int(sys.argv[2]) if len(sys.argv) > 2 else 365
    out = []
    seen: set = set()
    for i in range(days):
        d = start + timedelta(days=i)
        for p in gen_day(d, seen):
            out.append({"date": d.isoformat(), **p})
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2))
    # ユニーク率チェック
    texts = [o["text"] for o in out]
    uniq = len(set(texts))
    print(f"generated {len(out)} posts ({days}日×3) -> {OUT}")
    print(f"ユニーク文面: {uniq}/{len(texts)} ({uniq/len(texts)*100:.1f}%)")
    from collections import Counter
    print("style分布:", dict(Counter(o["style"] for o in out)))


if __name__ == "__main__":
    main()
