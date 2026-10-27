"""SQLite storage for Stock Radar."""
import json
import os
import sqlite3
import datetime as dt

HERE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(HERE, "data", "radar.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS prices (date TEXT, ticker TEXT, close REAL, PRIMARY KEY(date, ticker));
CREATE TABLE IF NOT EXISTS names (ticker TEXT PRIMARY KEY, name TEXT, sector TEXT);
CREATE TABLE IF NOT EXISTS fundamentals (ticker TEXT PRIMARY KEY, updated TEXT, data TEXT);
-- radar ranking, locked at the close of `date` (one snapshot per trading day)
CREATE TABLE IF NOT EXISTS picks (date TEXT, ticker TEXT, rank INTEGER, score REAL, stars INTEGER, price REAL,
  detail TEXT, PRIMARY KEY(date, ticker));
-- every option idea (LEAP or daily play) locked with its entry price, then marked to market each run
CREATE TABLE IF NOT EXISTS ideas (id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT, kind TEXT, ticker TEXT,
  contract TEXT, side TEXT, expiry TEXT, strike REAL, entry REAL, delta REAL, iv REAL, underlying REAL,
  reason TEXT, status TEXT DEFAULT 'open', exit_date TEXT, exit_price REAL, exit_reason TEXT, last REAL,
  last_date TEXT, peak REAL, qty INTEGER DEFAULT 1, UNIQUE(date, kind, contract));
CREATE TABLE IF NOT EXISTS marks (idea_id INTEGER, date TEXT, price REAL, underlying REAL, PRIMARY KEY(idea_id, date));
CREATE TABLE IF NOT EXISTS portfolio (name TEXT, date TEXT, value REAL, bench REAL, holdings TEXT, PRIMARY KEY(name, date));
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
"""


def now_str():
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def connect(path=DB_PATH):
    if os.path.dirname(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
    con = sqlite3.connect(path, timeout=30)
    con.executescript(SCHEMA)
    return con


def meta_get(con, k, default=None):
    r = con.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
    return json.loads(r[0]) if r else default


def meta_set(con, k, v):
    con.execute("INSERT OR REPLACE INTO meta(k,v) VALUES(?,?)", (k, json.dumps(v)))
    con.commit()


def save_prices(con, px):
    rows = [(d.strftime("%Y-%m-%d"), t, float(v)) for t in px.columns for d, v in px[t].dropna().items()]
    con.executemany("INSERT OR REPLACE INTO prices VALUES(?,?,?)", rows)
    con.commit()
    return len(rows)


def load_prices(con, tickers=None):
    import pandas as pd
    q = "SELECT date, ticker, close FROM prices"
    df = pd.read_sql_query(q, con)
    if df.empty:
        return pd.DataFrame()
    px = df.pivot(index="date", columns="ticker", values="close")
    px.index = pd.to_datetime(px.index)
    if tickers is not None:
        px = px[[t for t in tickers if t in px.columns]]
    return px.sort_index()
