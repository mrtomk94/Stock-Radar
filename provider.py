"""Market data. YahooProvider is used on GitHub; FakeProvider (STOCK_FAKE=1) is for offline testing."""
import io
import math
import os
import time
import logging

import numpy as np
import pandas as pd

log = logging.getLogger("radar")
INFO_KEYS = ["shortName", "sector", "industry", "marketCap", "trailingPE", "forwardPE", "trailingPegRatio",
             "revenueGrowth", "earningsGrowth", "profitMargins", "returnOnEquity", "debtToEquity",
             "freeCashflow", "dividendYield", "beta"]
FALLBACK = ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "AVGO", "TSLA", "BRK-B", "JPM", "LLY", "V", "UNH",
            "XOM", "MA", "COST", "HD", "PG", "JNJ", "NFLX", "ABBV", "CRM", "BAC", "KO", "AMD", "ORCL", "PEP",
            "TMO", "ADBE", "WMT", "MRK", "CSCO", "ACN", "LIN", "MCD", "ABT", "QCOM", "TXN", "INTU", "AMAT",
            "ISRG", "NOW", "CAT", "GE", "UBER", "BKNG", "PLTR", "PANW", "MU", "LRCX", "ANET", "CRWD", "SHOP"]


def get_provider():
    return FakeProvider() if os.environ.get("STOCK_FAKE") else YahooProvider()


class YahooProvider:
    UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/141.0 Safari/537.36"}

    def __init__(self):
        import yfinance  # noqa: F401  (fail early if missing)
        self.yf = yfinance

    def _wiki(self, url):
        import requests
        r = requests.get(url, headers=self.UA, timeout=30)
        r.raise_for_status()
        return pd.read_html(io.StringIO(r.text))

    def universe(self, which):
        out = {}
        if "sp500" in which:
            try:
                t = self._wiki("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies")[0]
                for _, r in t.iterrows():
                    out[str(r["Symbol"]).replace(".", "-")] = {"name": r.get("Security"), "sector": r.get("GICS Sector")}
            except Exception as e:
                log.warning("S&P 500 list failed: %s", e)
        if "nasdaq100" in which:
            try:
                for t in self._wiki("https://en.wikipedia.org/wiki/Nasdaq-100"):
                    col = next((c for c in t.columns if str(c).lower() in ("ticker", "symbol")), None)
                    if col is not None and len(t) >= 90:
                        for _, r in t.iterrows():
                            sym = str(r[col]).replace(".", "-")
                            out.setdefault(sym, {"name": r.get("Company"), "sector": r.get("GICS Sector")})
                        break
            except Exception as e:
                log.warning("Nasdaq-100 list failed: %s", e)
        if len(out) < 50:
            log.warning("using fallback universe")
            for s in FALLBACK:
                out.setdefault(s, {"name": s, "sector": None})
        return out

    def prices(self, tickers, period="3y"):
        frames = []
        tickers = list(dict.fromkeys(tickers))
        for i in range(0, len(tickers), 150):
            chunk = tickers[i:i + 150]
            for attempt in range(3):
                try:
                    d = self.yf.download(chunk, period=period, auto_adjust=True, progress=False, threads=True)
                    c = d["Close"] if isinstance(d.columns, pd.MultiIndex) else d[["Close"]].rename(columns={"Close": chunk[0]})
                    frames.append(c)
                    break
                except Exception as e:
                    log.warning("price chunk %d failed (%s), retry", i, e)
                    time.sleep(5 * (attempt + 1))
        px = pd.concat(frames, axis=1) if frames else pd.DataFrame()
        px.index = pd.to_datetime(px.index).tz_localize(None).normalize()
        return px.sort_index()

    def info(self, ticker):
        i = self.yf.Ticker(ticker).info or {}
        return {k: i.get(k) for k in INFO_KEYS}

    def expirations(self, ticker):
        return list(self.yf.Ticker(ticker).options or [])

    def chain(self, ticker, expiry, kind="call"):
        oc = self.yf.Ticker(ticker).option_chain(expiry)
        c = oc.calls if kind == "call" else oc.puts
        return c[["contractSymbol", "strike", "lastPrice", "bid", "ask", "volume", "openInterest", "impliedVolatility"]].copy()

    def calls(self, ticker, expiry):
        return self.chain(ticker, expiry, "call")


class FakeProvider:
    """Synthetic but realistic-looking data so the whole pipeline can be tested offline."""
    def __init__(self, seed=7):
        self.rng = np.random.default_rng(seed)
        self.tick = FALLBACK[:40] + ["SPY", "QQQ"]
        days = pd.bdate_range(end=pd.Timestamp("2026-12-31"), periods=800)
        r = np.random.default_rng(1)
        mu = {t: r.normal(0.10, 0.12) for t in self.tick}
        mu["SPY"] = 0.09
        sig = {t: r.uniform(0.18, 0.55) for t in self.tick}
        sig["SPY"] = 0.16
        mkt = r.normal(0, 0.16 / math.sqrt(252), len(days))
        data = {}
        for t in self.tick:
            beta = 0 if t == "SPY" else r.uniform(0.6, 1.5)
            idio = r.normal(0, sig[t] / math.sqrt(252), len(days))
            ret = mu[t] / 252 + (mkt if t == "SPY" else beta * mkt + idio * 0.8)
            data[t] = 100 * r.uniform(0.3, 5) * np.exp(np.cumsum(ret))
        self.px = pd.DataFrame(data, index=days)
        self.sig = sig

    def universe(self, which):
        return {t: {"name": f"{t} Corp", "sector": ["Tech", "Health", "Finance", "Energy", "Consumer"][i % 5]}
                for i, t in enumerate(self.tick) if t not in ("SPY", "QQQ")}

    def prices(self, tickers, period="3y"):
        cols = [t for t in tickers if t in self.px.columns]
        end = os.environ.get("STOCK_FAKE_END")
        px = self.px[cols]
        return px[px.index <= pd.Timestamp(end or "2026-10-27")]

    def info(self, ticker):
        r = np.random.default_rng(abs(hash(ticker)) % 2**32)
        return {"shortName": f"{ticker} Corp", "sector": "Tech", "marketCap": float(r.uniform(2e10, 3e12)),
                "trailingPE": float(r.uniform(8, 60)), "forwardPE": float(r.uniform(8, 45)),
                "trailingPegRatio": float(r.uniform(0.5, 3)), "revenueGrowth": float(r.normal(0.08, 0.1)),
                "earningsGrowth": float(r.normal(0.1, 0.2)), "profitMargins": float(r.uniform(-0.05, 0.4)),
                "returnOnEquity": float(r.uniform(-0.1, 0.6)), "debtToEquity": float(r.uniform(0, 250)),
                "freeCashflow": None, "dividendYield": None, "beta": float(r.uniform(0.6, 1.6))}

    def _spot(self, ticker):
        return float(self.prices([ticker]).iloc[-1, 0])

    def expirations(self, ticker):
        end = pd.Timestamp(os.environ.get("STOCK_FAKE_END", "2026-10-27"))
        return [(end + pd.Timedelta(days=d)).strftime("%Y-%m-%d") for d in (10, 24, 38, 52, 90, 200, 420, 800)]

    def calls(self, ticker, expiry):
        return self.chain(ticker, expiry, "call")

    def chain(self, ticker, expiry, kind="call"):
        from leaps import bs_call, bs_put
        S = self._spot(ticker)
        end = pd.Timestamp(os.environ.get("STOCK_FAKE_END", "2026-10-27"))
        T = (pd.Timestamp(expiry) - end).days / 365
        iv = self.sig.get(ticker, 0.3) * 0.9
        rows = []
        for k in np.round(np.linspace(0.4 * S, 1.6 * S, 41), 0):
            p = max(bs_call(S, k, T, 0.04, iv) if kind == "call" else bs_put(S, k, T, 0.04, iv), 0.01)
            rows.append(dict(contractSymbol=f"{ticker}{expiry.replace('-', '')[2:]}{'C' if kind == 'call' else 'P'}{int(k * 1000):08d}", strike=float(k),
                             lastPrice=p, bid=p * 0.97, ask=p * 1.03, volume=10, openInterest=500, impliedVolatility=iv))
        return pd.DataFrame(rows)
