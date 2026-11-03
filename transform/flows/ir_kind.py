"""ir-disclosure-dl のメタデータJSONから、決算短信の発行体が事業会社かREIT(投資法人)かを
判定する共通関数。

当初は「ixbrl_urlがあればREIT」で判別していた(2026-10-11時点では、iXBRLを取得して
いたのがREITだけだったため)。しかし事業会社の決算短信にもiXBRLが付くようになり
(2026年10月分は決算短信983件中823件がixbrl_urlを持つ。JT 2026-10-29の第3四半期決算
短信など)、「ixbrl_urlあり=REIT」と判定すると事業会社の決算短信が丸ごと取り込み対象
から外れていた。

iXBRLのファイル名にはタクソノミ識別子(tse-{a|q|s}{c|n}{ed|re}…)が含まれる:
  - 先頭の a/q/s: 通期/第1・第3四半期/中間
  - c/n: 連結/非連結
  - ed: 事業会社(例: tse-qcedifsm, tse-scedjpsy), re: REIT(例: tse-anrejpsm)
この ed / re でREITかを判定する。ixbrl_urlが無ければ事業会社(PDFのみ)。タクソノミを
判別できない未知のixbrl_urlは、従来どおりREIT扱いに倒す(事業会社向けPDFパーサーに
REITを流すと列がほぼNULLの行で汚染されるため、迷ったらREIT側のパイプラインに任せる)。
"""

from __future__ import annotations

import re

_TAXONOMY_RE = re.compile(r"tse-[aqs][cn](ed|re)", re.IGNORECASE)


def is_reit_ixbrl_url(ixbrl_url: str | None) -> bool:
    """ixbrl_urlがREIT(投資法人)のiXBRLか。無ければFalse(事業会社のPDFのみ)。"""
    if not ixbrl_url:
        return False
    m = _TAXONOMY_RE.search(ixbrl_url.rsplit("/", 1)[-1])
    if m is None:
        return True  # 判別不能は従来どおりREIT側に倒す
    return m.group(1).lower() == "re"
