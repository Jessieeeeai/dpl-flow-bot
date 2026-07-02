#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
大漂亮资金流秘籍 — GitHub Actions 信号机器人（纸面验证模式）
每小时运行：拉数据 → 评估 S10/S20 → 纸面持仓管理 → TG 推送（开单文字卡/关单K线图）
环境变量: COINGLASS_API_KEY / TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID
状态: state.json + trades_*.csv（由 workflow 提交回仓库）
"""
import csv
import json
import os
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

import tgx

HERE = os.path.dirname(os.path.abspath(__file__))
STATE_F = os.path.join(HERE, "state.json")
CG_KEY = os.environ.get("COINGLASS_API_KEY", "")
CG = "https://open-api-v4.coinglass.com"
COST_SIDE = 0.0007  # 单边 taker 0.05% + 滑点 0.02%


def cg(path, **params):
    url = f"{CG}{path}?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"CG-API-KEY": CG_KEY, "accept": "application/json"})
    for i in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                resp = json.loads(r.read().decode())
            time.sleep(1.0)
            data = resp.get("data") or []
            if str(resp.get("code")) != "0":
                print(f"[CG WARN] {path} code={resp.get('code')} {str(resp.get('msg'))[:80]}")
            return data
        except Exception as e:
            print(f"[CG RETRY] {path}: {e}")
            time.sleep(3)
    return []


def fetch_bars(pair, n=800):
    """1h K线，升序（F6 同源端点）"""
    end = int(time.time() * 1000)
    data = cg("/api/futures/price/history", exchange="Binance", symbol=pair,
              interval="1h", limit=1000, start_time=end - (n + 5) * 3600_000, end_time=end)
    bars = []
    for d in data:
        try:
            bars.append({"ts": int(d["time"]) // 1000,
                         "open": float(d["open"]), "high": float(d["high"]),
                         "low": float(d["low"]), "close": float(d["close"])})
        except (KeyError, ValueError, TypeError):
            continue
    return sorted(bars, key=lambda b: b["ts"])


def fetch_taker(pair):
    data = cg("/api/spot/taker-buy-sell-volume/history",
              exchange="Bybit", symbol=pair, interval="1h", limit=800)
    return {int(d["time"]) // 1000: float(d.get("taker_buy_volume_usd", 0)) -
            float(d.get("taker_sell_volume_usd", 0)) for d in data}


def fetch_funding(pair):
    data = cg("/api/futures/funding-rate/history",
              exchange="Bybit", symbol=pair, interval="8h", limit=100)
    out = []
    for d in data:
        v = float(d["close"])
        out.append((int(d["time"]) // 1000, v / 100 if abs(v) > 1e-3 else v))
    return sorted(out)


def rsi_wilder(vals, period=14):
    if len(vals) < period + 2:
        return None
    au = ad = 0.0
    for i in range(1, len(vals)):
        d = vals[i] - vals[i - 1]
        u, dn = max(d, 0), max(-d, 0)
        if i <= period:
            au += u / period
            ad += dn / period
        else:
            au = (au * (period - 1) + u) / period
            ad = (ad * (period - 1) + dn) / period
    return 100 - 100 / (1 + au / ad) if ad > 0 else 100.0


def features(bars, taker, fund):
    cur_hour = int(time.time()) // 3600 * 3600
    bars = [b for b in bars if b["ts"] < cur_hour]
    if len(bars) < 24 * 9:
        return None
    ts = [b["ts"] for b in bars]
    kl = {b["ts"]: b for b in bars}
    t0 = ts[-1]
    close = kl[t0]["close"]
    deltas = [taker.get(t, 0.0) for t in ts]
    cvd24_series = [sum(deltas[i - 24:i]) / kl[ts[i]]["close"] for i in range(24, len(ts))]
    p15 = sorted(cvd24_series)[int(len(cvd24_series) * 0.15)] if len(cvd24_series) >= 100 else None
    fr = None
    for ft, fv in reversed(fund):
        if ft <= t0 + 3600:
            fr = fv
            break
    h4h, h4l, h4c = {}, {}, {}
    for t in ts:
        b = t // 14400 * 14400
        h4h[b] = max(h4h.get(b, -1e18), kl[t]["high"])
        h4l[b] = min(h4l.get(b, 1e18), kl[t]["low"])
        h4c[b] = kl[t]["close"]
    done4 = [b for b in sorted(h4h) if b + 14400 <= t0 + 3600]
    d1h, d1l = {}, {}
    for t in ts:
        b = t // 86400 * 86400
        d1h[b] = max(d1h.get(b, -1e18), kl[t]["high"])
        d1l[b] = min(d1l.get(b, 1e18), kl[t]["low"])
    dk = [b for b in sorted(d1h) if b + 86400 <= t0 + 3600]
    hour = datetime.fromtimestamp(t0, tz=timezone.utc).hour
    return {
        "t0": t0, "close": close, "high": kl[t0]["high"], "low": kl[t0]["low"],
        "bars": bars, "cvd24": cvd24_series[-1] if cvd24_series else None, "p15": p15,
        "slope6": sum(deltas[-6:]) / close, "funding": fr,
        "resistance": max(h4h[b] for b in done4[-20:]) if len(done4) >= 20 else None,
        "h4_high3": max(h4h[b] for b in done4[-3:]) if len(done4) >= 3 else None,
        "h4_low3": min(h4l[b] for b in done4[-3:]) if len(done4) >= 3 else None,
        "d1_high5": max(d1h[b] for b in dk[-5:]) if len(dk) >= 5 else None,
        "d1_low5": min(d1l[b] for b in dk[-5:]) if len(dk) >= 5 else None,
        "h1_high6": max(kl[t]["high"] for t in ts[-7:-1]),
        "ma7": sum(kl[t]["close"] for t in ts[-168:]) / 168,
        "atr_pct": sum(kl[t]["high"] - kl[t]["low"] for t in ts[-24:]) / 24 / close,
        "rsi4h": rsi_wilder([h4c[b] for b in done4][-80:]),
        "us_open": hour in (13, 14, 15),
    }


def l01(f, direction, entry):
    if direction == "SHORT":
        h4, d1 = f["h4_high3"], f["d1_high5"]
        if h4 is None:
            return None
        if h4 > entry * 1.002:
            if d1 and d1 > h4 and d1 / entry - 1 < 0.05:
                return d1 * 1.003
            return h4 * 1.002
        return h4 * 1.002 if h4 > entry else entry * 1.008
    l4, d1 = f["h4_low3"], f["d1_low5"]
    if l4 is None:
        return None
    if l4 < entry * 0.998:
        if d1 and d1 < l4 and 1 - d1 / entry < 0.05:
            return d1 * 0.997
        return l4 * 0.998
    return l4 * 0.998 if l4 < entry else entry * 0.992


def append_csv(path, row, header):
    new = not os.path.exists(path)
    with open(path, "a", newline="") as fh:
        w = csv.writer(fh)
        if new:
            w.writerow(header)
        w.writerow(row)


def stats_of(path, expect_wr, expect_r):
    out = {"wins": 0, "losses": 0, "wr": 0.0, "total_r": 0.0, "total_pct": 0.0,
           "expect_wr": expect_wr, "expect_r": expect_r}
    if os.path.exists(path):
        for r in csv.DictReader(open(path)):
            pnl = float(r["pnl_pct"])
            risk = abs(float(r["stop"]) - float(r["entry"])) / float(r["entry"]) * 100
            out["total_pct"] += pnl
            out["total_r"] += pnl / risk if risk else 0
            out["wins" if pnl > 0 else "losses"] += 1
    n = out["wins"] + out["losses"]
    out["wr"] = out["wins"] / n if n else 0
    return out


def close_and_report(strat_code, strat_name, sym, pos, reason, exit_px, t0, bars,
                     csv_name, expect_wr, expect_r):
    d = -1 if pos["direction"] == "SHORT" else 1
    pnl = d * (exit_px - pos["entry"]) / pos["entry"] - 2 * COST_SIDE
    risk = abs(pos["stop"] - pos["entry"]) / pos["entry"]
    r_mult = pnl / risk if risk else 0
    path = os.path.join(HERE, csv_name)
    append_csv(path, [sym, datetime.fromtimestamp(pos["t_entry"], tz=timezone.utc).isoformat(),
                      datetime.fromtimestamp(t0, tz=timezone.utc).isoformat(),
                      pos["entry"], pos["stop"], pos["tp"], reason,
                      round(pnl * 100, 3), pos.get("lev", 1.0)],
               ["symbol", "t_entry", "t_exit", "entry", "stop", "tp", "reason", "pnl_pct", "lev"])
    stats = stats_of(path, expect_wr, expect_r)
    sig = dict(pos, exit=exit_px, t_exit=t0, r_mult=r_mult)
    caption = tgx.caption_close(pos.get("no", 0), strat_name, sym, reason, sig,
                                pnl * 100, r_mult, stats)
    try:
        import chart
        png = chart.render_close_chart("DPL-Flow", pos.get("no", 0), strat_code, sig, bars)
        if not tgx.send_photo(png, caption):
            tgx.send_message(caption)
    except Exception as e:
        print(f"[CHART FALLBACK] {e}")
        tgx.send_message(caption)


def manage_position(state_key, st, f, strat_code, strat_name, sym, csv_name, ew, er):
    pos = st.get("position")
    if not pos:
        return
    t0, h, l, c = f["t0"], f["high"], f["low"], f["close"]
    short = pos["direction"] == "SHORT"
    hit_stop = h >= pos["stop"] if short else l <= pos["stop"]
    hit_tp = l <= pos["tp"] if short else h >= pos["tp"]
    if hit_stop:
        close_and_report(strat_code, strat_name, sym, pos, "stop", pos["stop"], t0,
                         f["bars"], csv_name, ew, er)
        st["cooldown_until"] = t0 + 6 * 3600
        st["position"] = None
    elif hit_tp:
        close_and_report(strat_code, strat_name, sym, pos, "tp", pos["tp"], t0,
                         f["bars"], csv_name, ew, er)
        st["position"] = None
    elif t0 - pos["t_entry"] >= 72 * 3600:
        close_and_report(strat_code, strat_name, sym, pos, "time", c, t0,
                         f["bars"], csv_name, ew, er)
        st["position"] = None


def run_s10(state, f):
    st = state.setdefault("s10", {"position": None, "cooldown_until": 0, "last_bar": 0, "n": 0})
    if f["t0"] <= st["last_bar"]:
        return
    manage_position("s10", st, f, "S10", "S10·阻力衰竭做空", "BTC",
                    "trades_s10.csv", "55-62%", "+0.29R")
    px, res, fr = f["close"], f["resistance"], f["funding"]
    near = 0.02 if f["us_open"] else 0.015
    c1 = res and (res / px - 1) < near and px < res * 1.005
    c3 = f["cvd24"] is not None and f["cvd24"] < 0
    c5 = fr is not None and fr >= 0
    c6 = px < f["ma7"]
    if c1 and c3 and c5 and c6 and st["position"] is None and f["t0"] >= st["cooldown_until"]:
        stop = l01(f, "SHORT", px)
        if stop and stop > px and (stop / px - 1) <= 0.05:
            r = stop - px
            lev = round(min(0.01 / (stop / px - 1), 0.006 / f["atr_pct"], 5.0), 2)
            st["n"] += 1
            st["position"] = {"no": st["n"], "direction": "SHORT", "t_entry": f["t0"],
                              "entry": px, "stop": stop, "tp": px - 1.5 * r, "lev": lev}
            cond = (f"距阻力{(res / px - 1) * 100:.2f}% · CVD24h {f['cvd24']:.0f} · "
                    f"FR {fr * 100:+.4f}% · MA7下方")
            tgx.send_message(tgx.msg_open(st["n"], "S10·阻力衰竭做空", "BTC", "SHORT",
                                          px, stop, px - 1.5 * r, lev, cond))
    st["last_bar"] = f["t0"]


def run_s20(state, sym, f):
    st = state.setdefault("s20", {}).setdefault(
        sym, {"position": None, "cooldown_until": 0, "last_bar": 0})
    state["s20"].setdefault("n", 0)
    if f["t0"] <= st["last_bar"]:
        return
    manage_position("s20", st, f, "S20", "S20·恐慌衰竭做多", sym,
                    "trades_s20.csv", "45-50%", "+0.35R")
    px = f["close"]
    c1 = f["cvd24"] is not None and f["p15"] is not None and f["cvd24"] < f["p15"]
    c2 = f["slope6"] > 0
    c3 = f["rsi4h"] is not None and f["rsi4h"] > 50
    if c1 and c2 and c3 and st["position"] is None and f["t0"] >= st["cooldown_until"]:
        stop = l01(f, "LONG", px)
        if stop and stop < px and (1 - stop / px) <= 0.05:
            r = px - stop
            lev = round(min(0.005 / (1 - stop / px), 0.006 / f["atr_pct"], 5.0), 2)
            state["s20"]["n"] += 1
            no = state["s20"]["n"]
            st["position"] = {"no": no, "direction": "LONG", "t_entry": f["t0"],
                              "entry": px, "stop": stop, "tp": px + 1.5 * r, "lev": lev}
            cond = (f"CVD24h {f['cvd24']:.0f} < p15({f['p15']:.0f}) · 6h净流转正 · "
                    f"4hRSI {f['rsi4h']:.0f}>50")
            tgx.send_message(tgx.msg_open(no, "S20·恐慌衰竭做多", sym, "LONG",
                                          px, stop, px + 1.5 * r, lev, cond))
    st["last_bar"] = f["t0"]


def main():
    state = json.load(open(STATE_F)) if os.path.exists(STATE_F) else {}

    btc_bars = fetch_bars("BTCUSDT")
    btc_taker = fetch_taker("BTCUSDT")
    btc_fund = fetch_funding("BTCUSDT")
    fb = features(btc_bars, btc_taker, btc_fund)
    if fb:
        run_s10(state, fb)
        run_s20(state, "BTC", fb)
        print(f"BTC bar={datetime.fromtimestamp(fb['t0'], tz=timezone.utc)} "
              f"close={fb['close']:.0f} cvd={fb['cvd24']:.0f} fr={fb['funding']} "
              f"rsi4h={fb['rsi4h'] and round(fb['rsi4h'], 1)}")
    else:
        print("BTC 数据不足")

    eth_bars = fetch_bars("ETHUSDT")
    eth_taker = fetch_taker("ETHUSDT")
    fe = features(eth_bars, eth_taker, btc_fund)
    if fe:
        run_s20(state, "ETH", fe)
        print(f"ETH bar={datetime.fromtimestamp(fe['t0'], tz=timezone.utc)} "
              f"close={fe['close']:.0f} cvd={fe['cvd24']:.0f}")
    else:
        print("ETH 数据不足")

    json.dump(state, open(STATE_F, "w"), indent=2)
    print("state 已保存")


if __name__ == "__main__":
    main()
