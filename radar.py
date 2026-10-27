"""Stock Radar — long-term ranking, LEAP ideas and daily option plays, every one locked and then scored.

    python radar.py --once     # one full pass (what GitHub Actions runs after the close)
    STOCK_FAKE=1 python radar.py --once   # offline test with synthetic data
"""
import argparse
import datetime as dt
import json
import logging
import os
import sys
import traceback

import pandas as pd

import leaps
import provider
import sdb
import signals
import track

HERE = os.path.dirname(os.path.abspath(__file__))
log = logging.getLogger("radar")


def setup_logging():
    os.makedirs(os.path.join(HERE, "logs"), exist_ok=True)
    log.setLevel(logging.INFO)
    fh = logging.FileHandler(os.path.join(HERE, "logs", f"radar-{dt.date.today():%Y-%m-%d}.log"), encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(logging.Formatter("%(asctime)s %(message)s", "%H:%M:%S"))
    log.handlers = [fh, sh]


def load_cfg():
    with open(os.path.join(HERE, "config.json"), encoding="utf-8") as f:
        return json.load(f)


def universe(con, prov, cfg):
    cached = sdb.meta_get(con, "universe")
    if cached and cached["date"] >= (dt.date.today() - dt.timedelta(days=7)).isoformat():
        uni = cached["tickers"]
    else:
        uni = prov.universe(cfg.get("universe", ["sp500"]))
        sdb.meta_set(con, "universe", {"date": dt.date.today().isoformat(), "tickers": uni})
        con.executemany("INSERT OR REPLACE INTO names VALUES(?,?,?)",
                        [(t, str(v.get("name") or t), str(v.get("sector") or "")) for t, v in uni.items()])
        con.commit()
    for t in cfg.get("watchlist", []):
        uni.setdefault(t, {"name": t, "sector": ""})
    return uni


def update_fundamentals(con, prov, cfg, pf):
    have = {t: (u, json.loads(d)) for t, u, d in con.execute("SELECT ticker, updated, data FROM fundamentals")}
    cutoff = (dt.date.today() - dt.timedelta(days=cfg.get("fundamentals_max_age_days", 7))).isoformat()
    order = list(cfg.get("watchlist", [])) + list(pf.sort_values("mom_12_1", ascending=False).index)
    todo = [t for t in dict.fromkeys(order) if t in pf.index and (t not in have or have[t][0] < cutoff)]
    n = 0
    for t in todo[: cfg.get("fundamentals_per_run", 120)]:
        try:
            d = prov.info(t)
            con.execute("INSERT OR REPLACE INTO fundamentals VALUES(?,?,?)", (t, dt.date.today().isoformat(), json.dumps(d)))
            have[t] = (dt.date.today().isoformat(), d)
            n += 1
        except Exception as e:
            log.warning("info %s failed: %s", t, e)
    con.commit()
    log.info("fundamentals: %d refreshed, %d on file, %d still missing", n, len(have), len([t for t in pf.index if t not in have]))
    return {t: v[1] for t, v in have.items()}


def leap_ideas(prov, rad, cfg, day):
    today = dt.date.fromisoformat(day)
    names = list(rad.index[: cfg.get("leap_top", 20)]) + [t for t in cfg.get("watchlist", []) if t in rad.index]
    out = []
    for t in dict.fromkeys(names):
        S = float(rad.loc[t, "price"])
        for target in cfg.get("leap_target_deltas", [0.75]):
            try:
                c = leaps.pick_contract(prov, t, S, today, "call", cfg.get("leap_min_days", 365), 1000, target,
                                        cfg.get("risk_free_rate", 0.04), cfg.get("leap_min_open_interest", 20),
                                        cfg.get("leap_max_spread_pct", 15), "longest")
            except Exception as e:
                log.warning("LEAP %s failed: %s", t, e)
                c = None
            if c:
                c.update(ticker=t, score=float(rad.loc[t, "score"]), stars=int(rad.loc[t, "stars"]), target=target,
                         watch=t in cfg.get("watchlist", []))
                out.append(c)
    return out


def daily_plays(con, prov, rad, cfg, day):
    today = dt.date.fromisoformat(day)
    setups = signals.daily_setups(rad, cfg.get("daily_plays", 5))
    plays = []
    held = {r[0] for r in con.execute("SELECT ticker FROM ideas WHERE kind='daily' AND status='open'")}
    for t, s in setups.iterrows():
        try:
            c = leaps.pick_contract(prov, t, float(s.price), today, s.right, cfg.get("daily_min_days", 21),
                                    cfg.get("daily_max_days", 50), cfg.get("daily_target_delta", 0.45),
                                    cfg.get("risk_free_rate", 0.04), cfg.get("daily_min_open_interest", 100),
                                    cfg.get("daily_max_spread_pct", 10), "nearest")
        except Exception as e:
            log.warning("daily %s failed: %s", t, e)
            c = None
        if c:
            c.update(ticker=t, reason=s.reason, score=float(s.score), already=t in held)
            plays.append(c)
            if t not in held:  # one open play per ticker at a time
                track.add_idea(con, day, "daily", t, c, s.reason)
    log.info("daily plays: %d setups, %d with a liquid contract", len(setups), len(plays))
    return plays


def run_once():
    cfg = load_cfg()
    con = sdb.connect()
    prov = provider.get_provider()
    bench = cfg.get("benchmark", "SPY")
    uni = universe(con, prov, cfg)
    tickers = list(uni) + [bench, "QQQ"]
    log.info("downloading %d tickers", len(tickers))
    px = prov.prices(tickers, cfg.get("history_period", "3y"))
    px = px.dropna(axis=1, thresh=int(len(px) * 0.5))
    if px.empty or bench not in px.columns:
        raise RuntimeError("no price data")
    first = sdb.meta_get(con, "prices_loaded") is None
    sdb.save_prices(con, px if first else px.iloc[-10:])
    sdb.meta_set(con, "prices_loaded", True)
    day = px.index[-1].strftime("%Y-%m-%d")
    pf = signals.price_factors(px.drop(columns=[c for c in (bench, "QQQ") if c in px.columns]))
    fund = update_fundamentals(con, prov, cfg, pf)
    rad = signals.radar_score(pf, fund, cfg["weights"])
    for t in rad.index:
        if t in uni:
            rad.loc[t, "sector"] = rad.loc[t].get("sector") if pd.notna(rad.loc[t].get("sector")) else uni[t].get("sector")
    track.lock_picks(con, day, rad)
    closed = track.mark_ideas(con, prov, day, px, cfg)
    plays = daily_plays(con, prov, rad, cfg, day)
    lp = leap_ideas(prov, rad, cfg, day)
    opened = track.monthly_leaps(con, prov, day, [x for x in lp if x["target"] >= 0.7], cfg)
    val, bval = track.run_portfolio(con, day, px, rad, cfg, bench)
    log.info("day %s: %d ranked, %d plays, %d LEAP ideas, %d LEAPs opened, %d ideas closed, portfolio %.0f vs %s %.0f",
             day, len(rad), len(plays), len(lp), opened, closed, val, bench, bval)
    import report
    report.build(con, cfg, rad, px, plays, lp, day)
    with open(os.path.join(HERE, "data", "status.json"), "w") as f:
        json.dump({"updated": sdb.now_str(), "day": day, "ranked": len(rad), "plays": len(plays), "leaps": len(lp)}, f)
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.parse_args()
    setup_logging()
    try:
        sys.exit(run_once())
    except Exception:
        log.error("run failed:\n%s", traceback.format_exc())
        sys.exit(1)
