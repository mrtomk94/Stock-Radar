"""Quyền chọn bất thường (giống Ripple): chuỗi quyền chọn trễ ~15 phút công khai của Cboe.
Dữ liệu chỉ cho biết có giao dịch, không cho biết ai mua hay bán, nên chỉ để tham khảo."""
import datetime as dt
import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor

log = logging.getLogger("radar")
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/141.0 Safari/537.36"}
OCC = re.compile(r"^([A-Z.]{1,6})(\d{2})(\d{2})(\d{2})([CP])(\d{8})$")
DEFAULT_RULES = {"min_volume": 1000, "min_ratio": 2, "min_premium": 1_000_000, "max_dte": 60, "top": 40}


def parse_occ(code):
    m = OCC.match(str(code or ""))
    if not m:
        return None
    return {"root": m[1], "expiry": f"20{m[2]}-{m[3]}-{m[4]}", "type": "call" if m[5] == "C" else "put", "strike": int(m[6]) / 1000}


def parse_chain(js):
    d = (js or {}).get("data") or {}
    if not isinstance(d.get("options"), list):
        return None
    out = []
    for o in d["options"]:
        occ = parse_occ(o.get("option"))
        if not occ:
            continue
        last = float(o.get("last_trade_price") or 0)
        mid = (float(o.get("bid") or 0) + float(o.get("ask") or 0)) / 2
        out.append({**occ, "code": o["option"], "volume": int(o.get("volume") or 0), "oi": int(o.get("open_interest") or 0),
                    "price": last if last > 0 else (mid if mid > 0 else 0), "iv": o.get("iv")})
    return {"symbol": str(d.get("symbol", "")).lstrip("_"), "price": float(d.get("current_price") or 0) or None, "contracts": out}


def flag(chain, today, rules):
    res = []
    for c in chain["contracts"]:
        dte = (dt.date.fromisoformat(c["expiry"]) - today).days
        prem = c["volume"] * c["price"] * 100
        if (c["volume"] >= rules["min_volume"] and c["volume"] > rules["min_ratio"] * c["oi"]
                and prem >= rules["min_premium"] and 0 <= dte <= rules["max_dte"]):
            res.append({"ticker": chain["symbol"], "contract": c["code"], "type": c["type"], "strike": c["strike"],
                        "expiry": c["expiry"], "dte": dte, "volume": c["volume"], "oi": c["oi"],
                        "ratio": round(c["volume"] / c["oi"], 1) if c["oi"] else None, "price": c["price"], "premium": prem,
                        "underlying": chain["price"], "moneyness": (c["strike"] / chain["price"] - 1) if chain["price"] else None})
    return res


def flow(chain):
    cp = sum(c["volume"] * c["price"] * 100 for c in chain["contracts"] if c["type"] == "call")
    pp = sum(c["volume"] * c["price"] * 100 for c in chain["contracts"] if c["type"] == "put")
    cv = sum(c["volume"] for c in chain["contracts"] if c["type"] == "call")
    pv = sum(c["volume"] for c in chain["contracts"] if c["type"] == "put")
    return {"call_prem": cp, "put_prem": pp, "pc_ratio": (pv / cv) if cv else None}


def _fetch(t):
    import requests
    r = requests.get(f"https://cdn.cboe.com/api/global/delayed_quotes/options/{t.replace('-', '.')}.json", headers=UA, timeout=60)
    r.raise_for_status()
    return parse_chain(r.json())


def sweep(tickers, rules=None, today=None):
    rules = {**DEFAULT_RULES, **(rules or {})}
    today = today or dt.date.today()
    if os.environ.get("STOCK_FAKE"):
        return {"scanned": 0, "failed": 0, "flagged": [], "flows": {}, "rules": rules, "note": "chế độ thử (STOCK_FAKE)"}
    flagged, flows, failed = [], {}, []
    with ThreadPoolExecutor(max_workers=3) as ex:
        for t, res in zip(tickers, ex.map(lambda x: _safe(x), tickers)):
            if isinstance(res, Exception) or res is None:
                failed.append(t)
                continue
            flagged += flag(res, today, rules)
            flows[t] = flow(res)
    flagged.sort(key=lambda f: -f["premium"])
    log.info("Quyền chọn bất thường: %d giao dịch từ %d mã (%d lỗi)", len(flagged), len(flows), len(failed))
    return {"scanned": len(flows), "failed": len(failed), "flagged": flagged[: rules["top"]], "flows": flows, "rules": rules}


def _safe(t):
    try:
        return _fetch(t)
    except Exception as ex:  # một mã lỗi không làm hỏng cả lượt
        return ex
