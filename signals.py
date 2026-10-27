"""Long-term radar score + daily swing setups, computed only from data available at the last close."""
import math

import numpy as np
import pandas as pd


def _rsi(s, n=14):
    d = s.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def price_factors(px):
    """One row per ticker from a close-price DataFrame (index=dates)."""
    rows = {}
    for t in px.columns:
        s = px[t].dropna()
        if len(s) < 260:
            continue
        p = s.iloc[-1]
        ma50, ma200 = s.iloc[-50:].mean(), s.iloc[-200:].mean()
        ema20 = s.ewm(span=20, adjust=False).mean()
        r = s.pct_change().dropna()
        rows[t] = dict(
            price=p, mom_12_1=s.iloc[-21] / s.iloc[-252] - 1, mom_6=p / s.iloc[-126] - 1, ret_1m=p / s.iloc[-21] - 1,
            ret_1y=p / s.iloc[-252] - 1, dist200=p / ma200 - 1, dist50=p / ma50 - 1, golden=float(ma50 > ma200),
            dd=p / s.iloc[-252:].max() - 1, vol=r.iloc[-252:].std() * math.sqrt(252),
            rsi=float(_rsi(s).iloc[-1]), dist_ema20=p / ema20.iloc[-1] - 1,
            touched_ema20=float((s.iloc[-3:] <= ema20.iloc[-3:] * 1.005).any()),
            low_touched_ema20=float((s.iloc[-3:] >= ema20.iloc[-3:] * 0.995).any()))
    return pd.DataFrame(rows).T


def _rank(x, higher_better=True):
    x = pd.to_numeric(x, errors="coerce")
    r = x.rank(pct=True) if higher_better else (-x).rank(pct=True)
    return r.fillna(0.5)


def radar_score(pf, fund, weights):
    """Composite 0–100. Percentile ranks inside the universe; missing data counts as neutral (0.5)."""
    f = pd.DataFrame({t: fund.get(t, {}) for t in pf.index}).T.reindex(pf.index)
    pos = lambda c: pd.to_numeric(f.get(c), errors="coerce").where(lambda v: v > 0)
    comp = pd.DataFrame(index=pf.index)
    comp["momentum"] = _rank(pf.mom_12_1)
    comp["trend"] = (_rank(pf.dist200.clip(-0.5, 0.5)) + pf.golden.astype(float)) / 2
    comp["quality"] = (_rank(f.get("returnOnEquity")) + _rank(f.get("profitMargins"))) / 2
    comp["growth"] = (_rank(f.get("revenueGrowth")) + _rank(f.get("earningsGrowth"))) / 2
    comp["value"] = (_rank(pos("forwardPE"), False) + _rank(pos("trailingPegRatio"), False)) / 2
    comp["risk"] = (_rank(pf.vol, False) + _rank(pf.dd)) / 2
    w = pd.Series(weights)
    score = (comp[w.index] * w).sum(1) / w.sum() * 100
    pct = score.rank(pct=True)
    stars = pd.cut(pct, [0, .35, .65, .85, .95, 1.01], labels=[1, 2, 3, 4, 5]).astype(int)
    out = pf.join(comp).join(f[[c for c in ["shortName", "sector", "forwardPE", "revenueGrowth", "returnOnEquity",
                                            "profitMargins", "marketCap"] if c in f.columns]])
    out["score"], out["stars"] = score, stars
    return out.sort_values("score", ascending=False)


def daily_setups(rad, n=5):
    """Pullback-style swing setups for short-dated options.
    CALL: long-term uptrend (above 200-day, 50>200, 6-month gain) that just pulled back to the 20-day EMA, RSI 38–58.
    PUT : long-term downtrend that just bounced up to the 20-day EMA, RSI 42–62."""
    r = rad
    bull = r[(r.dist200 > 0) & (r.golden == 1) & (r.mom_6 > 0) & (r.dist_ema20.between(-0.02, 0.015))
             & (r.touched_ema20 == 1) & (r.rsi.between(38, 58))].copy()
    bull["right"], bull["strength"] = "call", bull.mom_6 + bull.score / 200
    bear = r[(r.dist200 < 0) & (r.golden == 0) & (r.mom_6 < 0) & (r.dist_ema20.between(-0.015, 0.02))
             & (r.low_touched_ema20 == 1) & (r.rsi.between(42, 62))].copy()
    bear["right"], bear["strength"] = "put", -bear.mom_6 + (100 - bear.score) / 200
    both = pd.concat([bull.nlargest(n, "strength"), bear.nlargest(max(1, n // 2), "strength")])
    both["reason"] = [
        (f"Xu hướng tăng (trên MA200, +{x.mom_6 * 100:.0f}% 6 tháng) chỉnh về EMA20, RSI {x.rsi:.0f}" if x.right == "call"
         else f"Xu hướng giảm (dưới MA200, {x.mom_6 * 100:.0f}% 6 tháng) hồi lên EMA20, RSI {x.rsi:.0f}")
        for x in both.itertuples()]
    return both
