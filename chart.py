#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""关单复盘 K 线图（样式对齐 F6 trade_report：蜡烛+TP虚线+SL红线+entry/exit标记）。
出图失败由调用方降级为纯文字。图上只用英文标注，中文放 caption。"""
from datetime import datetime

GRAY = "#888780"
RED = "#E24B4A"
TEAL = "#1D9E75"
BLUE = "#378ADD"
UP = "#1D9E75"
DOWN = "#E24B4A"
SL_RED = "#A32D2D"


def _d(ts):
    return datetime.utcfromtimestamp(ts)


def _aggregate(bars, n):
    out = []
    for i in range(0, len(bars), n):
        ch = bars[i:i + n]
        out.append({"ts": ch[0]["ts"], "open": ch[0]["open"], "close": ch[-1]["close"],
                    "high": max(b["high"] for b in ch), "low": min(b["low"] for b in ch)})
    return out


def render_close_chart(brand, no, strat_code, sig, bars):
    """sig: {direction, entry, stop, tp, exit, t_entry, t_exit, r_mult}
    bars: [{ts, open, high, low, close}] 1h 升序。返回 PNG bytes。"""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates

    t_in, t_out = sig["t_entry"], sig["t_exit"]
    win = [b for b in bars if t_in - 24 * 3600 <= b["ts"] <= t_out + 12 * 3600]
    if len(win) < 5:
        raise ValueError("窗口K线不足")
    bar_hours = 1
    if len(win) > 240:
        bar_hours = 4
        win = _aggregate(win, 4)

    fig, ax = plt.subplots(figsize=(8, 4.5), dpi=150)
    w = bar_hours / 24 * 0.65
    for b in win:
        x = mdates.date2num(_d(b["ts"]))
        up = b["close"] >= b["open"]
        c = UP if up else DOWN
        ax.vlines(x, b["low"], b["high"], color=c, linewidth=0.7, alpha=0.9)
        body = abs(b["close"] - b["open"]) or (b["high"] - b["low"]) * 0.01 + 0.01
        ax.bar(x, body, width=w, bottom=min(b["open"], b["close"]),
               color=c, alpha=0.9 if up else 0.85, linewidth=0)

    x0, x1 = _d(t_in), _d(t_out)
    lo = min(min(b["low"] for b in win), sig["stop"], sig["entry"], sig["exit"], sig["tp"])
    hi = max(max(b["high"] for b in win), sig["stop"], sig["entry"], sig["exit"], sig["tp"])
    rng = hi - lo
    ax.hlines(sig["tp"], x0, x1, color=TEAL, linestyle="--", linewidth=1.3)
    ax.annotate(f"TP {sig['tp']:,.0f}", (x0, sig["tp"]), fontsize=8, color=TEAL,
                ha="left", va="bottom")
    ax.hlines(sig["stop"], x0, x1, color=SL_RED, linewidth=1.8)
    ax.annotate(f"SL {sig['stop']:,.0f}", (x0, sig["stop"]), fontsize=8, color=SL_RED,
                ha="left", va="bottom")
    ax.set_ylim(lo - 0.07 * rng, hi + 0.07 * rng)

    short = sig["direction"] == "SHORT"
    ax.plot([x0], [sig["entry"]], marker="v" if short else "^", color=BLUE,
            markersize=10, zorder=5)
    ax.annotate(f"entry {sig['entry']:,.0f}", (x0, sig["entry"]), fontsize=9, color=BLUE,
                ha="left", va="top" if short else "bottom",
                xytext=(6, -10 if short else 10), textcoords="offset points")
    exit_col = TEAL if sig.get("r_mult", 0) >= 0 else RED
    ax.plot([x1], [sig["exit"]], marker="o", color=exit_col, markersize=9, zorder=5)
    ax.annotate(f"exit {sig['exit']:,.0f}", (x1, sig["exit"]), fontsize=9, color=exit_col,
                ha="right", xytext=(-6, 10), textcoords="offset points")

    rr = sig.get("r_mult", 0)
    ax.set_title(f"[{brand}] #{no:03d} {strat_code} {'SHORT' if short else 'LONG'}  "
                 f"{rr:+.2f}R", fontsize=11, loc="left")
    ax.yaxis.set_major_formatter(lambda v, _: f"${v:,.0f}")
    span_days = (win[-1]["ts"] - win[0]["ts"]) / 86400
    fmt = "%m-%d %H:%M" if span_days < 4 else "%m-%d"
    ax.xaxis.set_major_locator(mdates.AutoDateLocator(maxticks=7))
    ax.xaxis.set_major_formatter(mdates.DateFormatter(fmt))
    ax.tick_params(axis="x", labelsize=8)
    ax.grid(True, linewidth=0.3, alpha=0.4)
    for sp in ("top", "right"):
        ax.spines[sp].set_visible(False)
    fig.tight_layout()

    import io
    buf = io.BytesIO()
    fig.savefig(buf, format="png")
    plt.close(fig)
    return buf.getvalue()
