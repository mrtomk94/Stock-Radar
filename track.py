"""Honest bookkeeping: lock every suggestion with its entry price, then follow it with real later prices."""
import json
import logging
import datetime as dt

import numpy as np
import pandas as pd

import leaps
import sdb

log = logging.getLogger("radar")


# ---------------------------------------------------------------- radar snapshots
def lock_picks(con, day, rad):
    if con.execute("SELECT 1 FROM picks WHERE date=? LIMIT 1", (day,)).fetchone():
        return 0
    rows = []
    for rank, (t, r) in enumerate(rad.iterrows(), 1):
        det = {k: (None if pd.isna(r.get(k)) else float(r.get(k))) for k in
               ("momentum", "trend", "quality", "growth", "value", "risk", "mom_12_1", "dist200")}
        rows.append((day, t, rank, float(r.score), int(r.stars), float(r.price), json.dumps(det)))
    con.executemany("INSERT OR IGNORE INTO picks VALUES(?,?,?,?,?,?,?)", rows)
    con.commit()
    return len(rows)


def pick_scoreboard(con, px, bench):
    """Forward return of each star group vs the benchmark, using one snapshot per month (so samples don't overlap)."""
    snaps = [r[0] for r in con.execute("SELECT DISTINCT date FROM picks ORDER BY date")]
    monthly, seen = [], set()
    for d in snaps:
        if d[:7] not in seen:
            seen.add(d[:7])
            monthly.append(d)
    out = {}
    idx = px.index
    for h, label in ((21, "1 tháng"), (63, "3 tháng"), (126, "6 tháng"), (252, "12 tháng")):
        res = {s: [] for s in range(1, 6)}
        for d in monthly:
            i0 = idx.searchsorted(pd.Timestamp(d))
            if i0 >= len(idx) or i0 + h >= len(idx):
                continue
            p0, p1 = px.iloc[i0], px.iloc[i0 + h]
            b = p1[bench] / p0[bench] - 1
            for t, s in con.execute("SELECT ticker, stars FROM picks WHERE date=?", (d,)):
                if t in px.columns and pd.notna(p0.get(t)) and pd.notna(p1.get(t)):
                    res[s].append(p1[t] / p0[t] - 1 - b)
        out[label] = {s: (np.mean(v) * 100, np.mean(np.array(v) > 0) * 100, len(v)) if v else None for s, v in res.items()}
    return out, len(monthly)


# ---------------------------------------------------------------- option ideas
def add_idea(con, day, kind, ticker, c, reason, qty=1):
    try:
        con.execute("""INSERT INTO ideas(date,kind,ticker,contract,side,expiry,strike,entry,delta,iv,underlying,reason,
                       last,last_date,peak,qty) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (day, kind, ticker, c["contract"], c["right"], c["expiry"], c["strike"], c["mid"], c["delta"],
                     c["iv"], c["underlying"], reason, c["mid"], day, c["mid"], qty))
        con.commit()
        return True
    except Exception:
        return False


def mark_ideas(con, prov, day, px, cfg):
    """Mark every open idea to today's mid and apply its exit rule."""
    today = dt.date.fromisoformat(day)
    rows = con.execute("SELECT id,kind,ticker,contract,side,expiry,strike,entry,date,peak FROM ideas WHERE status='open'").fetchall()
    closed = 0
    for iid, kind, t, contract, side, exp, K, entry, opened, peak in rows:
        S = float(px[t].dropna().iloc[-1]) if t in px.columns else None
        dte = (dt.date.fromisoformat(exp) - today).days
        try:
            m = leaps.current_price(prov, contract, t, exp, side) if dte >= 0 else None
        except Exception as e:
            log.warning("mark %s failed: %s", contract, e)
            m = None
        if m is None and dte < 0 and S is not None:
            m = max(S - K, 0) if side == "call" else max(K - S, 0)  # expired: worth intrinsic
        if m is None:
            continue
        con.execute("INSERT OR REPLACE INTO marks VALUES(?,?,?,?)", (iid, day, m, S))
        con.execute("UPDATE ideas SET last=?, last_date=?, peak=MAX(COALESCE(peak,0),?) WHERE id=?", (m, day, m, iid))
        held = (today - dt.date.fromisoformat(opened)).days
        chg = m / entry - 1 if entry else 0
        why = None
        if kind == "daily":
            if chg >= cfg.get("daily_take_profit", 0.5):
                why = f"chốt lời +{cfg.get('daily_take_profit', 0.5) * 100:.0f}%"
            elif chg <= -cfg.get("daily_stop_loss", 0.5):
                why = f"cắt lỗ -{cfg.get('daily_stop_loss', 0.5) * 100:.0f}%"
            elif held >= cfg.get("daily_max_hold_days", 14):
                why = "hết thời gian giữ"
            elif dte <= 7:
                why = "sắp hết hạn"
        else:
            if dte <= cfg.get("leap_exit_days_before_expiry", 90):
                why = "còn 90 ngày tới hạn"
            elif held >= cfg.get("leap_max_hold_days", 365):
                why = "giữ đủ 12 tháng"
        if why:
            con.execute("UPDATE ideas SET status='closed', exit_date=?, exit_price=?, exit_reason=? WHERE id=?",
                        (day, m, why, iid))
            closed += 1
    con.commit()
    return closed


def idea_stats(con, kind):
    rows = con.execute("SELECT entry, COALESCE(exit_price,last), qty, status FROM ideas WHERE kind=?", (kind,)).fetchall()
    if not rows:
        return None
    pl = [(x - e) * 100 * q for e, x, q, s in rows if e and x is not None]
    cost = sum(e * 100 * q for e, x, q, s in rows if e)
    closed = [(x - e) * 100 * q for e, x, q, s in rows if s == "closed" and e and x is not None]
    return dict(n=len(rows), open=sum(1 for r in rows if r[3] == "open"), closed=len(closed),
                win=(np.mean(np.array(closed) > 0) * 100 if closed else None), pl=sum(pl), cost=cost,
                ret=(sum(pl) / cost * 100 if cost else 0))


# ---------------------------------------------------------------- virtual stock portfolio
def run_portfolio(con, day, px, rad, cfg, bench):
    """'Radar Top N': equal-weight the top N, rebalance on the first run of each month. Benchmark: same $ in SPY."""
    name, start = "radar_top", cfg.get("portfolio_start", 10000)
    last = con.execute("SELECT date, value, bench, holdings FROM portfolio WHERE name=? ORDER BY date DESC LIMIT 1",
                       (name,)).fetchone()
    p = px.iloc[-1]
    if last is None:
        hold = {}
        value = start
        bshares = start / p[bench]
        sdb.meta_set(con, "bench_shares", bshares)
    else:
        hold = json.loads(last[3])
        value = sum(sh * p[t] for t, sh in hold.items() if t in p and pd.notna(p[t]))
        bshares = sdb.meta_get(con, "bench_shares")
    if last is None or last[0][:7] != day[:7]:
        top = [t for t in rad.index if t != bench][:cfg.get("portfolio_top_n", 10)]
        hold = {t: value / len(top) / p[t] for t in top}
        sdb.meta_set(con, "portfolio_rebalanced", day)
    con.execute("INSERT OR REPLACE INTO portfolio VALUES(?,?,?,?,?)",
                (name, day, float(value), float(bshares * p[bench]), json.dumps(hold)))
    con.commit()
    return value, bshares * p[bench]


def monthly_leaps(con, prov, day, leap_list, cfg):
    """On the first run of each month, open virtual LEAP positions in the two best ideas with the monthly budget."""
    if sdb.meta_get(con, "leap_month") == day[:7] or not leap_list:
        return 0
    budget = cfg.get("leap_monthly_budget", 2000) / 2
    held = {r[0] for r in con.execute("SELECT ticker FROM ideas WHERE kind='leap' AND status='open'")}
    n = 0
    for L in leap_list:
        if L["delta"] < 0.6 or L["ticker"] in held:
            continue
        qty = int(budget // (L["mid"] * 100))
        if qty < 1:
            continue
        if add_idea(con, day, "leap", L["ticker"], L, f"Radar {L['score']:.0f} điểm, delta {L['delta']}", qty):
            n += 1
        if n == 2:
            break
    sdb.meta_set(con, "leap_month", day[:7])
    return n


# ---------------------------------------------------------------- price-only backtest (what the radar would have done)
def backtest_price_only(px, bench, top_n=20):
    """Monthly: buy the top N by (12-1 momentum rank + above-200-day), hold a month. Uses today's index members,
    so it is flattered by survivorship (yesterday's losers that left the index are missing)."""
    m_ends = px.resample("ME").last().index
    dates = [px.index[px.index.searchsorted(d, side="right") - 1] for d in m_ends]
    dates = [d for d in dates if px.index.get_loc(d) >= 252]
    eq, be, curve = 1.0, 1.0, []
    for d0, d1 in zip(dates, dates[1:]):
        i = px.index.get_loc(d0)
        win = px.iloc[: i + 1]
        mom = win.iloc[-21] / win.iloc[-252] - 1
        trend = win.iloc[-1] > win.iloc[-200:].mean()
        sc = mom.rank(pct=True) + trend.astype(float)
        sc = sc.drop(labels=[bench], errors="ignore").dropna()
        top = sc.nlargest(top_n).index
        r = (px.loc[d1, top] / px.loc[d0, top] - 1).mean()
        rb = px.loc[d1, bench] / px.loc[d0, bench] - 1
        eq *= 1 + r
        be *= 1 + rb
        curve.append((d1.strftime("%Y-%m"), eq, be))
    return curve
