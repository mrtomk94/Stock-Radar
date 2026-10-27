"""reports/latest.html — the Stock Radar page."""
import datetime as dt
import json
import os
from html import escape as e

import pandas as pd

import sdb
import track

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "reports")


def pct(x, d=1, sign=True):
    if x is None or pd.isna(x):
        return "<span class='muted'>—</span>"
    v = x * 100
    cls = "pos" if v > 0 else ("neg" if v < 0 else "")
    return f"<span class='{cls}'>{v:+.{d}f}%</span>" if sign else f"{v:.{d}f}%"


def money(x):
    cls = "pos" if x > 0 else ("neg" if x < 0 else "")
    return f"<span class='{cls}'>{'+' if x > 0 else ''}{x:,.0f}$</span>"


def stars(n):
    return "<span class='star'>" + "★" * int(n) + "</span><span class='muted'>" + "★" * (5 - int(n)) + "</span>"


def line_chart(series, labels=("Danh mục", "SPY"), colors=("#3ddc97", "#8b98a3")):
    """series: list of (label, a, b)."""
    if len(series) < 2:
        return "<p class='muted'>Biểu đồ sẽ hiện sau vài ngày dữ liệu.</p>"
    W, H, P = 900, 220, 36
    vals = [v for _, a, b in series for v in (a, b)]
    lo, hi = min(vals), max(vals)
    hi = hi if hi > lo else lo + 1
    X = lambda i: P + (W - 2 * P) * i / (len(series) - 1)
    Y = lambda v: H - 22 - (H - 40) * (v - lo) / (hi - lo)
    paths = ""
    for k, col in ((1, colors[0]), (2, colors[1])):
        pts = " ".join(f"{X(i):.1f},{Y(s[k]):.1f}" for i, s in enumerate(series))
        paths += f"<polyline points='{pts}' fill='none' stroke='{col}' stroke-width='2'/>"
    ax = (f"<text x='4' y='{Y(hi) + 4:.0f}' class='ax'>{hi:,.0f}</text><text x='4' y='{Y(lo) + 4:.0f}' class='ax'>{lo:,.0f}</text>"
          f"<text x='{P}' y='{H - 4}' class='ax'>{e(series[0][0])}</text>"
          f"<text x='{W - P}' y='{H - 4}' class='ax' text-anchor='end'>{e(series[-1][0])}</text>")
    leg = "".join(f"<span><i style='background:{c}'></i>{l}</span>" for l, c in zip(labels, colors))
    return f"<svg viewBox='0 0 {W} {H}' class='chart'>{ax}{paths}</svg><div class='legend'>{leg}</div>"


def radar_table(rad, rows):
    h = ("<tr><th>#</th><th>Mã</th><th style='text-align:left'>Tên / ngành</th><th>Giá</th><th>Hạng</th><th>Điểm</th><th>1 năm</th>"
         "<th>So MA200</th><th>Từ đỉnh</th><th>P/E tới</th><th>Tăng DT</th><th>ROE</th><th>Đà · Xu hướng · Chất lượng · Tăng trưởng · Giá trị · Rủi ro</th></tr>")
    body = ""
    for i, t in enumerate(rows, 1):
        if t not in rad.index:
            continue
        r = rad.loc[t]
        bars = "".join(f"<i style='height:{max(r[c], 0.04) * 22:.0f}px' title='{c} {r[c] * 100:.0f}'></i>"
                       for c in ("momentum", "trend", "quality", "growth", "value", "risk"))
        fpe = r.get("forwardPE")
        body += (f"<tr><td>{int(list(rad.index).index(t)) + 1}</td><td><b>{e(t)}</b></td>"
                 f"<td class='nm'>{e(str(r.get('shortName') or '')[:24])}<br><span class='muted'>{e(str(r.get('sector') or ''))}</span></td>"
                 f"<td>{r.price:,.2f}</td><td>{stars(r.stars)}</td><td><b>{r.score:.0f}</b></td><td>{pct(r.ret_1y, 0)}</td>"
                 f"<td>{pct(r.dist200, 0)}</td><td>{pct(r.dd, 0)}</td>"
                 f"<td>{'' if fpe is None or pd.isna(fpe) else f'{fpe:.1f}'}</td><td>{pct(r.get('revenueGrowth'), 0)}</td>"
                 f"<td>{pct(r.get('returnOnEquity'), 0, False) if pd.notna(r.get('returnOnEquity')) else '—'}</td><td class='bars'>{bars}</td></tr>")
    return f"<div class='scroll'><table>{h}{body}</table></div>"


def build(con, cfg, rad, px, plays, leap_list, day):
    os.makedirs(OUT, exist_ok=True)
    bench = cfg.get("benchmark", "SPY")
    # ---- today's daily option plays
    if plays:
        cards = ""
        for p in plays:
            col = "var(--green)" if p["right"] == "call" else "var(--red)"
            cards += (f"<div class='play'><div><b style='color:{col}'>{e(p['ticker'])} {'CALL' if p['right'] == 'call' else 'PUT'}</b> "
                      f"${p['strike']:g} · hết hạn {p['expiry']} ({p['days']} ngày)</div>"
                      f"<div class='big'>${p['mid']:.2f} <span class='muted'>/ hợp đồng ${p['cost']:,}</span></div>"
                      f"<div class='muted'>delta {p['delta']:+.2f} · IV {p['iv'] * 100:.0f}% · hòa vốn ${p['breakeven']:,.2f} "
                      f"({pct(p['move_needed'])} khi đáo hạn) · OI {p['oi']:,} · spread {p['spread_pct']}%</div>"
                      f"<div class='why'>{e(p['reason'])}{' · <span class=muted>đang theo dõi lệnh mở trước đó</span>' if p.get('already') else ''}</div></div>")
        plays_html = f"<div class='plays'>{cards}</div>"
    else:
        plays_html = "<p class='muted'>Hôm nay không có mã nào khớp điều kiện (hoặc hợp đồng không đủ thanh khoản).</p>"
    rules = (f"Luật theo dõi: chốt lời +{cfg.get('daily_take_profit', .5) * 100:.0f}%, cắt lỗ −{cfg.get('daily_stop_loss', .5) * 100:.0f}%, "
             f"tối đa {cfg.get('daily_max_hold_days', 14)} ngày hoặc khi còn 7 ngày tới hạn. Giá vào = giá giữa (mid) lúc đề xuất.")
    # ---- tracked ideas
    def ideas_table(kind, limit=40):
        rows = con.execute("""SELECT date,ticker,side,strike,expiry,entry,COALESCE(exit_price,last),status,exit_reason,qty,reason
                              FROM ideas WHERE kind=? ORDER BY date DESC, id DESC LIMIT ?""", (kind, limit)).fetchall()
        if not rows:
            return "<p class='muted'>Chưa có vị thế nào.</p>"
        b = ""
        for d, t, side, k, ex, en, now, st, why, q, rs in rows:
            ch = (now / en - 1) if en else 0
            b += (f"<tr><td>{d}</td><td><b>{e(t)}</b> {'C' if side == 'call' else 'P'} {k:g}</td><td>{ex}</td><td>{q:g}</td>"
                  f"<td>${en:.2f}</td><td>${now:.2f}</td><td>{pct(ch, 0)}</td><td>{money((now - en) * 100 * q)}</td>"
                  f"<td class='muted'>{'đang mở' if st == 'open' else e(why or '')}</td></tr>")
        return ("<div class='scroll'><table><tr><th>Ngày vào</th><th>Hợp đồng</th><th>Hết hạn</th><th>SL</th><th>Vào</th>"
                f"<th>Hiện tại</th><th>%</th><th>Lời/lỗ</th><th>Trạng thái</th></tr>{b}</table></div>")

    def stat_cards(kind, label):
        s = track.idea_stats(con, kind)
        if not s:
            return ""
        win = "" if s["win"] is None else f"thắng {s['win']:.0f}% lệnh đã đóng · "
        return (f"<div><span>{label}</span><b>{money(s['pl'])}</b><em>{s['n']} lệnh · {s['open']} đang mở · "
                f"{win}vốn ${s['cost']:,.0f} · {s['ret']:+.1f}%</em></div>")

    # ---- LEAP ideas
    lrows = ""
    for L in sorted(leap_list, key=lambda x: (-x["score"], -x["target"])):
        lrows += (f"<tr><td><b>{e(L['ticker'])}</b>{' ⭐' if L.get('watch') else ''}</td><td>{L['score']:.0f}</td><td>${L['underlying']:,.2f}</td>"
                  f"<td>{L['expiry']}</td><td>${L['strike']:g}</td><td>{L['delta']:.2f}</td><td>${L['mid']:.2f}</td>"
                  f"<td>${L['cost']:,}</td><td>${L['breakeven']:,.2f}</td><td>{pct(L['move_needed'])}</td>"
                  f"<td>{L['extrinsic_pct_yr']:.1f}%</td><td>{L['leverage']}×</td><td>{L['iv'] * 100:.0f}%</td><td>{L['spread_pct']}%</td></tr>")
    leap_html = ("<div class='scroll'><table><tr><th>Mã</th><th>Điểm</th><th>Giá CP</th><th>Hết hạn</th><th>Strike</th><th>Delta</th>"
                 "<th>Giá</th><th>1 HĐ</th><th>Hòa vốn</th><th>Cần tăng</th><th>Phí thời gian/năm</th><th>Đòn bẩy</th><th>IV</th>"
                 f"<th>Spread</th></tr>{lrows}</table></div>") if lrows else "<p class='muted'>Không lấy được chuỗi quyền chọn hôm nay.</p>"
    # ---- portfolio
    pr = con.execute("SELECT date, value, bench FROM portfolio WHERE name='radar_top' ORDER BY date").fetchall()
    port_html = line_chart([(d, v, b) for d, v, b in pr], ("Radar Top 10", bench)) if pr else ""
    if pr:
        hold = json.loads(con.execute("SELECT holdings FROM portfolio WHERE name='radar_top' ORDER BY date DESC LIMIT 1").fetchone()[0])
        port_html = (f"<div class='kpis'><div><span>Radar Top {cfg.get('portfolio_top_n', 10)}</span><b>${pr[-1][1]:,.0f}</b>"
                     f"<em>{pct(pr[-1][1] / pr[0][1] - 1)} từ {pr[0][0]}</em></div><div><span>{bench} cùng số vốn</span>"
                     f"<b>${pr[-1][2]:,.0f}</b><em>{pct(pr[-1][2] / pr[0][2] - 1)}</em></div><div><span>Đang giữ</span>"
                     f"<b style='font-size:15px'>{', '.join(hold)}</b><em>cân bằng lại đầu mỗi tháng</em></div></div>" + port_html)
    # ---- scoreboard
    sb, nsnap = track.pick_scoreboard(con, px, bench)
    sb_rows = ""
    for h, g in sb.items():
        cells = "".join(f"<td>{'—' if not g[s] else pct(g[s][0] / 100) + f' <span class=muted>({g[s][1]:.0f}% thắng · n={g[s][2]})</span>'}</td>"
                        for s in (5, 4, 3, 2, 1))
        sb_rows += f"<tr><td>{h}</td>{cells}</tr>"
    sb_html = (f"<div class='scroll'><table><tr><th>Sau</th><th>★★★★★</th><th>★★★★</th><th>★★★</th><th>★★</th><th>★</th></tr>{sb_rows}</table></div>"
               f"<p class='note'>Mỗi ô = lời/lỗ trung bình <b>hơn hoặc kém {bench}</b> của các mã ở hạng đó, tính từ ảnh chụp đầu mỗi tháng "
               f"(đang có {nsnap}). Bảng tự đầy dần; cần ≥ 12 tháng mới đáng tin.</p>")
    # ---- backtest
    bt = track.backtest_price_only(px, bench)
    bt_html = ""
    if bt:
        yrs = len(bt) / 12
        cagr = bt[-1][1] ** (1 / yrs) - 1 if yrs else 0
        bc = bt[-1][2] ** (1 / yrs) - 1 if yrs else 0
        bt_html = (f"<div class='kpis'><div><span>Top 20 đà + xu hướng</span><b>{pct(bt[-1][1] - 1, 0)}</b><em>{pct(cagr)}/năm</em></div>"
                   f"<div><span>{bench}</span><b>{pct(bt[-1][2] - 1, 0)}</b><em>{pct(bc)}/năm</em></div></div>"
                   + line_chart([(m, a * 100, b * 100) for m, a, b in bt], ("Top 20 (giá 1 → 100)", bench))
                   + "<p class='note'>Chỉ dùng giá (đà 12-1 tháng + trên MA200), mua đầu tháng giữ 1 tháng. Dùng danh sách mã <b>hiện tại</b> "
                     "nên kết quả bị phóng đại (những mã từng tệ đã bị loại khỏi chỉ số không có mặt). Chỉ để tham khảo, không phải bằng chứng.</p>")
    top = list(rad.index[: cfg.get("radar_show", 40)])
    watch = [t for t in cfg.get("watchlist", []) if t in rad.index]
    stat = "".join(x for x in (stat_cards("daily", "Option play hằng ngày (1 HĐ mỗi lệnh)"),
                               stat_cards("leap", f"Ví LEAP (${cfg.get('leap_monthly_budget', 2000):,}/tháng)")) if x)
    page = f"""<!doctype html><html lang="vi"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Stock Radar</title>
<style>
:root{{--bg:#07090b;--panel:#0d1116;--line:#1b2127;--fg:#d7dee4;--muted:#7d8a95;--green:#3ddc97;--amber:#f5a524;--red:#ff6b5b}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--fg);font:14px/1.5 ui-monospace,Consolas,monospace}}
main{{max-width:1240px;margin:0 auto;padding:20px 16px 60px}}h1{{font-size:20px;margin:0 0 4px;color:var(--green);letter-spacing:.06em}}
h2{{font-size:13px;letter-spacing:.14em;color:var(--muted);margin:32px 0 10px;text-transform:uppercase}}
.muted{{color:var(--muted)}}.pos{{color:var(--green)}}.neg{{color:var(--red)}}.star{{color:var(--amber)}}
.scroll{{overflow-x:auto;border:1px solid var(--line);border-radius:6px}}table{{border-collapse:collapse;width:100%;font-size:12.5px}}
th,td{{padding:6px 9px;border-bottom:1px solid var(--line);text-align:right;white-space:nowrap}}th{{color:var(--muted);font-weight:500;background:var(--panel)}}
td:first-child,th:first-child,td.nm{{text-align:left}}td.nm{{white-space:normal;min-width:150px}}
td.bars{{text-align:left}}td.bars i{{display:inline-block;width:9px;margin-right:2px;background:var(--green);vertical-align:bottom;opacity:.8}}
.kpis{{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:10px;margin-bottom:10px}}.kpis div{{background:var(--panel);border:1px solid var(--line);border-radius:6px;padding:12px}}
.kpis div>span{{display:block;color:var(--muted);font-size:11px;letter-spacing:.08em;text-transform:uppercase}}.kpis div>b{{display:block;font-size:20px;margin:4px 0}}.kpis em{{font-style:normal;color:var(--muted);font-size:12px}}
.plays{{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:10px}}.play{{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:12px;display:grid;gap:4px}}
.play .big{{font-size:20px}}.play .why{{font-size:12.5px;border-top:1px solid var(--line);padding-top:6px;margin-top:2px}}
.note{{color:var(--muted);font-size:12.5px;max-width:900px}}.warn{{background:#1c1408;border:1px solid #5a4415;color:#f2d19b;border-radius:6px;padding:10px 14px;font-size:12.5px}}
.chart{{width:100%;height:auto;background:var(--panel);border:1px solid var(--line);border-radius:6px}}.chart .ax{{fill:var(--muted);font-size:11px}}
.legend{{display:flex;gap:14px;margin-top:6px;font-size:12px}}.legend i{{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:6px}}
</style></head><body><main>
<h1>STOCK RADAR</h1>
<div class="muted">Dữ liệu tới phiên {day} · cập nhật {sdb.now_str()} · {len(rad):,} mã được chấm điểm</div>
<p class="warn">Công cụ nghiên cứu cá nhân, không phải lời khuyên đầu tư. Option có thể mất 100% tiền bỏ vào. Mọi đề xuất bên dưới được
<b>khóa giá lúc đưa ra</b> và theo dõi bằng giá thật về sau — xem bảng chấm điểm trước khi tin bất kỳ tín hiệu nào.</p>
<div class="kpis">{stat}</div>
<h2>Option play hôm nay · 3–7 tuần</h2>{plays_html}<p class="note">{e(rules)}</p>
<h2>Theo dõi option play</h2>{ideas_table('daily')}
<h2>LEAP gợi ý · call ≥ 12 tháng cho các mã điểm cao</h2>{leap_html}
<p class="note">Delta 0.75 ≈ "thay cổ phiếu" (ít phí thời gian, đi gần như cổ phiếu); delta 0.50 rẻ hơn, đòn bẩy cao hơn, dễ mất trắng hơn.
"Phí thời gian/năm" = phần giá không phải giá trị nội tại, chia cho số năm — càng thấp càng tốt. ⭐ = mã trong watchlist.</p>
<h2>Ví LEAP ảo · mỗi tháng mua 2 LEAP tốt nhất</h2>{ideas_table('leap')}
<h2>Radar dài hạn · top {len(top)}</h2>{radar_table(rad, top)}
<h2>Watchlist của bạn</h2>{radar_table(rad, watch)}
<p class="note">Điểm 0–100 = xếp hạng trong toàn bộ S&amp;P 500 + Nasdaq-100: đà tăng 12 tháng (25%), xu hướng so MA200 (15%),
chất lượng ROE/biên lợi nhuận (20%), tăng trưởng doanh thu/lợi nhuận (20%), định giá P/E tới &amp; PEG (10%), rủi ro biến động/sụt giảm (10%).
★★★★★ = top 5%.</p>
<h2>Danh mục ảo Radar Top 10 vs {bench}</h2>{port_html}
<h2>Chấm điểm radar · các hạng sao có thật sự thắng {bench}?</h2>{sb_html}
<h2>Kiểm tra lại quá khứ (chỉ dùng giá)</h2>{bt_html}
</main></body></html>"""
    path = os.path.join(OUT, "latest.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(page)
    return path
