"""Option math and contract selection (LEAPs and short-dated daily plays)."""
import math
import datetime as dt
import logging

log = logging.getLogger("radar")


def _N(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def bs_call(S, K, T, r, s):
    if T <= 0 or s <= 0:
        return max(S - K, 0.0)
    d1 = (math.log(S / K) + (r + s * s / 2) * T) / (s * math.sqrt(T))
    return S * _N(d1) - K * math.exp(-r * T) * _N(d1 - s * math.sqrt(T))


def bs_put(S, K, T, r, s):
    return bs_call(S, K, T, r, s) - S + K * math.exp(-r * T)


def delta(S, K, T, r, s, right="call"):
    if T <= 0 or s <= 0:
        return 1.0 if (right == "call" and S > K) else 0.0
    d1 = (math.log(S / K) + (r + s * s / 2) * T) / (s * math.sqrt(T))
    return _N(d1) if right == "call" else _N(d1) - 1


def mid_price(row):
    b, a = row.get("bid") or 0, row.get("ask") or 0
    if b > 0 and a > 0:
        return (b + a) / 2, (a - b) / ((a + b) / 2) * 100
    lp = row.get("lastPrice") or 0
    return (lp, 100.0) if lp > 0 else (None, None)


def pick_contract(prov, ticker, S, today, right, min_days, max_days, target_delta, r, min_oi, max_spread,
                  prefer="longest"):
    """Best contract near target |delta| inside [min_days, max_days] to expiry. Returns dict or None."""
    exps = []
    for e in prov.expirations(ticker):
        d = (dt.date.fromisoformat(e) - today).days
        if min_days <= d <= max_days:
            exps.append((d, e))
    if not exps:
        return None
    exps.sort(reverse=(prefer == "longest"))
    if prefer == "nearest":
        target_days = (min_days + max_days) / 2
        exps.sort(key=lambda x: abs(x[0] - target_days))
    for days, e in exps[:2]:
        try:
            ch = prov.chain(ticker, e, right)
        except Exception as ex:
            log.warning("chain %s %s failed: %s", ticker, e, ex)
            continue
        T = days / 365
        best = None
        for _, row in ch.iterrows():
            m, spr = mid_price(row)
            iv = row.get("impliedVolatility") or 0
            if not m or iv <= 0.01 or (row.get("openInterest") or 0) < min_oi or spr > max_spread:
                continue
            K = float(row["strike"])
            dl = delta(S, K, T, r, iv, right)
            gap = abs(abs(dl) - target_delta)
            if best is None or gap < best[0]:
                intrinsic = max(S - K, 0) if right == "call" else max(K - S, 0)
                best = (gap, dict(contract=row["contractSymbol"], right=right, expiry=e, days=days, strike=K,
                                  mid=round(m, 2), spread_pct=round(spr, 1), iv=round(iv, 3), delta=round(dl, 2),
                                  oi=int(row.get("openInterest") or 0), underlying=S,
                                  breakeven=round(K + m if right == "call" else K - m, 2),
                                  move_needed=round(((K + m) / S - 1) if right == "call" else ((K - m) / S - 1), 4),
                                  extrinsic_pct_yr=round(max(m - intrinsic, 0) / S / T * 100, 2),
                                  leverage=round(abs(dl) * S / m, 1), cost=round(m * 100)))
        if best and best[0] < 0.12:
            return best[1]
    return None


def current_price(prov, contract, ticker, expiry, right):
    """Today's mid for a contract we are tracking."""
    ch = prov.chain(ticker, expiry, right)
    row = ch[ch.contractSymbol == contract]
    if row.empty:
        return None
    m, _ = mid_price(row.iloc[0].to_dict())
    return m
