#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
大漂亮资金流秘籍 — 策略赛马版（纸面验证，5策略并跑）
记账标准：每单保证金$1000×10倍杠杆=$10,000名义；每策略本金$10,000；
余额<$1000无法开仓即淘汰。每周一UTC发排位战报。
用法: python bot.py [demo]
"""
import csv
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

import tgx

HERE = os.path.dirname(os.path.abspath(__file__))
STATE_F = os.path.join(HERE, "state.json")
TRADES_F = os.path.join(HERE, "trades.csv")
CG_KEY = os.environ.get("COINGLASS_API_KEY", "")
CG = "https://open-api-v4.coinglass.com"
COST_SIDE = 0.0007
MARGIN = 1000.0
NOTIONAL = 10000.0
START_EQ = 10000.0

STRATS = {
    "S10": {"name": "阻力衰竭空·MA7", "side": "S", "gate": "ma7", "buf": 1.002, "syms": ["BTC"]},
    "S11": {"name": "阻力衰竭空·MA13", "side": "S", "gate": "ma13", "buf": 1.004, "syms": ["BTC"]},
    "S12": {"name": "阻力衰竭空·维加斯", "side": "S", "gate": "vegas", "buf": 1.002, "syms": ["BTC"]},
    "S13": {"name": "阻力衰竭空·费率动量", "side": "S", "gate": "ma7", "buf": 1.002,
            "syms": ["BTC"], "d4": True},
    "S20": {"name": "恐慌衰竭多·p15", "side": "L", "q": "p15", "syms": ["BTC", "ETH"]},
    "S21": {"name": "恐慌衰竭多·p10", "side": "L", "q": "p10", "syms": ["BTC", "ETH"]},
    # C组：信号与S组相同，仓位改用C层动态（对照组，隔离仓位层贡献）
    "C10": {"name": "S10信号+C层仓位", "side": "S", "gate": "ma7", "buf": 1.002,
            "syms": ["BTC"], "sizing": "clayer"},
    "C20": {"name": "S20信号+C层仓位", "side": "L", "q": "p15",
            "syms": ["BTC", "ETH"], "sizing": "clayer"},
}


def cg(path, **params):
    url = f"{CG}{path}?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"CG-API-KEY": CG_KEY, "accept": "application/json"})
    for i in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                resp = json.loads(r.read().decode())
            time.sleep(1.0)
            if str(resp.get("code")) != "0":
                print(f"[CG WARN] {path} code={resp.get('code')}")
            return resp.get("data") or []
        except Exception as e:
            print(f"[CG RETRY] {path}: {e}")
            time.sleep(3)
    return []


def fetch_bars(pair, n=800):
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


def ema_last(vals, span):
    k = 2 / (span + 1)
    e = vals[0]
    for v in vals[1:]:
        e = v * k + e * (1 - k)
    return e


def features(bars, taker, fund, t0=None):
    if t0 is None:
        cur_hour = int(time.time()) // 3600 * 3600
        bars = [b for b in bars if b["ts"] < cur_hour]
    else:
        bars = [b for b in bars if b["ts"] <= t0]
    if len(bars) < 24 * 14:
        return None
    ts = [b["ts"] for b in bars]
    kl = {b["ts"]: b for b in bars}
    closes = [kl[t]["close"] for t in ts]
    t0 = ts[-1]
    close = closes[-1]
    deltas = [taker.get(t, 0.0) for t in ts]
    cvd24 = [sum(deltas[i - 24:i]) / closes[i] for i in range(24, len(ts))]
    srt = sorted(cvd24)
    fr, fr_prev24 = None, None
    for ft, fv in reversed(fund):
        if fr is None and ft <= t0 + 3600:
            fr = fv
        if ft <= t0 - 24 * 3600 + 3600:
            fr_prev24 = fv
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
        "bars": bars, "cvd24": cvd24[-1] if cvd24 else None,
        "p15": srt[int(len(srt) * 0.15)] if len(srt) >= 100 else None,
        "p10": srt[int(len(srt) * 0.10)] if len(srt) >= 100 else None,
        "slope6": sum(deltas[-6:]) / close, "funding": fr, "funding_prev24": fr_prev24,
        "resistance": max(h4h[b] for b in done4[-20:]) if len(done4) >= 20 else None,
        "h4_high3": max(h4h[b] for b in done4[-3:]) if len(done4) >= 3 else None,
        "h4_low3": min(h4l[b] for b in done4[-3:]) if len(done4) >= 3 else None,
        "d1_high5": max(d1h[b] for b in dk[-5:]) if len(dk) >= 5 else None,
        "d1_low5": min(d1l[b] for b in dk[-5:]) if len(dk) >= 5 else None,
        "atr_pct": sum(kl[t]["high"] - kl[t]["low"] for t in ts[-24:]) / 24 / close,
        "ma7": sum(closes[-168:]) / 168,
        "ma13": sum(closes[-312:]) / min(len(closes), 312),
        "vegas": min(ema_last(closes[-500:], 144), ema_last(closes[-500:], 169)),
        "rsi4h": rsi_wilder([h4c[b] for b in done4][-80:]),
        "us_open": hour in (13, 14, 15),
    }


def l01(f, direction, entry, buf=1.002):
    if direction == "SHORT":
        h4, d1 = f["h4_high3"], f["d1_high5"]
        if h4 is None:
            return None
        b2 = buf + 0.001
        if h4 > entry * 1.002:
            if d1 and d1 > h4 and d1 / entry - 1 < 0.05:
                return d1 * b2
            return h4 * buf
        return h4 * buf if h4 > entry else entry * 1.008
    l4, d1 = f["h4_low3"], f["d1_low5"]
    if l4 is None:
        return None
    if l4 < entry * 0.998:
        if d1 and d1 < l4 and 1 - d1 / entry < 0.05:
            return d1 * 0.997
        return l4 * 0.998
    return l4 * 0.998 if l4 < entry else entry * 0.992


def check_entry(sid, cfg, f):
    """返回 (stop, cond_desc) 或 None"""
    px = f["close"]
    if cfg["side"] == "S":
        res, fr = f["resistance"], f["funding"]
        near = 0.02 if f["us_open"] else 0.015
        gate = {"ma7": px < f["ma7"], "ma13": px < f["ma13"], "vegas": px < f["vegas"]}[cfg["gate"]]
        if not (res and (res / px - 1) < near and px < res * 1.005
                and f["cvd24"] is not None and f["cvd24"] < 0
                and fr is not None and fr >= 0 and gate):
            return None
        d4_tag = ""
        if cfg.get("d4"):
            prev = f.get("funding_prev24")
            if prev is None or fr <= prev:
                return None
            d4_tag = f" · FR上升中({prev * 100:+.4f}→{fr * 100:+.4f}%)"
        stop = l01(f, "SHORT", px, cfg["buf"])
        if not (stop and stop > px and stop / px - 1 <= 0.05):
            return None
        return stop, (f"距阻力{(res / px - 1) * 100:.2f}% · CVD {f['cvd24']:.0f} · "
                      f"FR {fr * 100:+.4f}% · {cfg['gate'].upper()}下方{d4_tag}")
    thr = f[cfg["q"]]
    if not (f["cvd24"] is not None and thr is not None and f["cvd24"] < thr
            and f["slope6"] > 0 and f["rsi4h"] is not None and f["rsi4h"] > 50):
        return None
    stop = l01(f, "LONG", px)
    if not (stop and stop < px and 1 - stop / px <= 0.05):
        return None
    return stop, (f"CVD {f['cvd24']:.0f} < {cfg['q']}({thr:.0f}) · 6h净流转正 · "
                  f"4hRSI {f['rsi4h']:.0f}>50")


def append_trade(row):
    new = not os.path.exists(TRADES_F)
    with open(TRADES_F, "a", newline="") as fh:
        w = csv.writer(fh)
        if new:
            w.writerow(["strat", "no", "symbol", "t_entry", "t_exit", "direction",
                        "entry", "stop", "tp", "reason", "pnl_pct", "pnl_usd", "equity"])
        w.writerow(row)


def strat_stats(sid):
    wins = losses = 0
    total_usd = 0.0
    if os.path.exists(TRADES_F):
        for r in csv.DictReader(open(TRADES_F)):
            if r["strat"] != sid:
                continue
            p = float(r["pnl_usd"])
            total_usd += p
            if p > 0:
                wins += 1
            else:
                losses += 1
    n = wins + losses
    return {"wins": wins, "losses": losses, "n": n,
            "wr": wins / n if n else 0, "total_usd": total_usd}


EXEC_F = os.path.join(HERE, "exec_log.csv")


def log_exec(event, sid="", value=""):
    """E层执行日志：影子熔断/补检单/数据异常，只记录不干预"""
    new = not os.path.exists(EXEC_F)
    with open(EXEC_F, "a", newline="") as fh:
        w = csv.writer(fh)
        if new:
            w.writerow(["ts", "event", "strat", "value"])
        w.writerow([int(time.time()), event, sid, value])


def shadow_check(sid, t0):
    """影子熔断（Kurisko规则，纸面阶段只记录）：单日累计≤-1.5R 或 当日连亏≥3笔"""
    day = datetime.fromtimestamp(t0, tz=timezone.utc).strftime("%Y-%m-%d")
    day_r, streak = 0.0, 0
    if os.path.exists(TRADES_F):
        for r in csv.DictReader(open(TRADES_F)):
            if r["strat"] != sid or not r["t_exit"].startswith(day):
                continue
            e, s = float(r["entry"]), float(r["stop"])
            risk = abs(s - e) / e
            rm = (float(r["pnl_pct"]) / 100) / risk if risk else 0
            day_r += rm
            streak = streak + 1 if float(r["pnl_usd"]) <= 0 else 0
    if day_r <= -1.5:
        log_exec("SHADOW_HALT_DAYR", sid, round(day_r, 2))
    if streak >= 3:
        log_exec("SHADOW_HALT_STREAK", sid, streak)


def close_position(sid, cfg, book, ss, sym, pos, reason, exit_px, t0, bars):
    d = -1 if pos["direction"] == "SHORT" else 1
    pnl_pct = d * (exit_px - pos["entry"]) / pos["entry"] - 2 * COST_SIDE
    pnl_usd = pos.get("notional", NOTIONAL) * pnl_pct
    ss["equity"] = round(ss["equity"] + pnl_usd, 2)
    risk = abs(pos["stop"] - pos["entry"]) / pos["entry"]
    r_mult = pnl_pct / risk if risk else 0
    append_trade([sid, pos["no"], sym,
                  datetime.fromtimestamp(pos["t_entry"], tz=timezone.utc).isoformat(),
                  datetime.fromtimestamp(t0, tz=timezone.utc).isoformat(),
                  pos["direction"], pos["entry"], pos["stop"], pos["tp"], reason,
                  round(pnl_pct * 100, 3), round(pnl_usd, 2), ss["equity"]])
    st = strat_stats(sid)
    shadow_check(sid, t0)
    sig = dict(pos, exit=exit_px, t_exit=t0, r_mult=r_mult)
    caption = tgx.caption_close_race(sid, cfg["name"], sym, reason, sig,
                                     pnl_pct * 100, pnl_usd, r_mult, ss["equity"], st)
    try:
        import chart
        png = chart.render_close_chart("DPL-Flow", pos["no"], sid, sig, bars)
        if not tgx.send_photo(png, caption):
            tgx.send_message(caption)
    except Exception as e:
        print(f"[CHART FALLBACK] {e}")
        tgx.send_message(caption)
    if ss["equity"] < MARGIN and not ss.get("eliminated"):
        ss["eliminated"] = True
        tgx.send_message(f"💅 <b>{tgx.BRAND}</b>\n"
                         f"💀 <b>{sid} {cfg['name']} 淘汰出局</b>\n"
                         f"账户余额 ${ss['equity']:,.0f} 不足单笔保证金 ${MARGIN:,.0f}")


def run_strategy(sid, cfg, feats, state, raw, fund):
    ss = state.setdefault(sid, {"equity": START_EQ, "n": 0, "eliminated": False, "books": {}})
    for sym in cfg["syms"]:
        f = feats.get(sym)
        if not f:
            continue
        bars, tk = raw[sym]
        book = ss["books"].setdefault(sym, {"position": None, "cooldown_until": 0, "last_bar": 0})
        if f["t0"] <= book["last_bar"]:
            continue
        kl = {b["ts"]: b for b in f["bars"]}
        seq = sorted(t for t in kl if book["last_bar"] < t <= f["t0"])
        pos = book["position"]
        for t_i in seq:
            b = kl[t_i]
            if pos:
                short = pos["direction"] == "SHORT"
                hit_stop = b["high"] >= pos["stop"] if short else b["low"] <= pos["stop"]
                hit_tp = b["low"] <= pos["tp"] if short else b["high"] >= pos["tp"]
                if hit_stop:
                    close_position(sid, cfg, book, ss, sym, pos, "stop", pos["stop"], t_i, f["bars"])
                    book["cooldown_until"] = t_i + 6 * 3600
                    pos = None
                elif hit_tp:
                    close_position(sid, cfg, book, ss, sym, pos, "tp", pos["tp"], t_i, f["bars"])
                    pos = None
                elif t_i - pos["t_entry"] >= 72 * 3600:
                    close_position(sid, cfg, book, ss, sym, pos, "time", b["close"], t_i, f["bars"])
                    pos = None
            if (pos is None and not ss.get("eliminated")
                    and ss["equity"] >= MARGIN and t_i >= book["cooldown_until"]):
                # cron跳档补检：历史bar需按该bar重算特征（无前视）
                fi = f if t_i == f["t0"] else features(bars, tk, fund, t0=t_i)
                if fi and fi["t0"] == t_i:
                    hit = check_entry(sid, cfg, fi)
                    if hit:
                        stop, cond = hit
                        px = fi["close"]
                        r = abs(px - stop)
                        direction = "SHORT" if cfg["side"] == "S" else "LONG"
                        tp = px - 1.5 * r if direction == "SHORT" else px + 1.5 * r
                        if cfg.get("sizing") == "clayer":
                            risk_frac = r / px
                            budget = 0.01 if cfg["side"] == "S" else 0.005
                            lev = min(budget / risk_frac, 0.006 / fi["atr_pct"], 5.0)
                            notional = round(ss["equity"] * lev, 2)
                        else:
                            notional = NOTIONAL
                        ss["n"] += 1
                        if t_i != f["t0"]:
                            log_exec("BACKFILL_ENTRY", sid, t_i)
                        log_exec("ENTRY_ATR", sid, round(fi["atr_pct"], 5))
                        pos = {"no": ss["n"], "direction": direction, "t_entry": t_i,
                               "entry": px, "stop": stop, "tp": tp, "notional": notional}
                        tgx.send_message(tgx.msg_open_race(sid, cfg["name"], ss["n"], sym, direction,
                                                           px, stop, tp, ss["equity"], cond, notional))
        book["position"] = pos
        book["last_bar"] = f["t0"]


# 回测身份证（预期胜率 / 每笔均R），周报做符合度评估用
EXPECT = {"S10": (55, "+0.29R"), "S11": (51, "+0.35R"), "S12": (52, "+0.30R"),
          "S13": (64, "+0.55R"), "S20": (47, "+0.35R"), "S21": (45, "+0.40R"),
          "C10": (55, "+0.29R"), "C20": (47, "+0.35R")}


def streaks(sid):
    mw = ml = cw = cl = 0
    if os.path.exists(TRADES_F):
        for r in csv.DictReader(open(TRADES_F)):
            if r["strat"] != sid:
                continue
            if float(r["pnl_usd"]) > 0:
                cw += 1; cl = 0
            else:
                cl += 1; cw = 0
            mw, ml = max(mw, cw), max(ml, cl)
    return mw, ml


def weekly_report(state, now_ts, force=False):
    dt = datetime.fromtimestamp(now_ts, tz=timezone.utc)
    week_key = dt.strftime("%G-W%V")
    if not force and (dt.weekday() != 6 or state.get("week_reported") == week_key):
        return  # 每周日发
    rows = []
    for sid, cfg in STRATS.items():
        ss = state.get(sid, {"equity": START_EQ})
        st = strat_stats(sid)
        wk_usd = 0.0
        if os.path.exists(TRADES_F):
            for r in csv.DictReader(open(TRADES_F)):
                if r["strat"] == sid and \
                        (now_ts - datetime.fromisoformat(r["t_exit"]).timestamp()) <= 7 * 86400:
                    wk_usd += float(r["pnl_usd"])
        rows.append((sid, cfg["name"], ss.get("equity", START_EQ), wk_usd, st,
                     ss.get("eliminated", False)))
    rows.sort(key=lambda x: -x[2])
    lines = [f"💅 <b>{tgx.BRAND}</b>",
             f"🏁 <b>策略赛马周报</b> — {dt.strftime('%Y-%m-%d')} (周日)",
             "━━━━━━━━━━━━━━━",
             f"规则: S组$1,000×10x固定 · C组C层动态 · 各$10,000本金", ""]
    medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣"]
    for i, (sid, name, eq, wk, st, elim) in enumerate(rows):
        tag = "💀已淘汰" if elim else f"{(eq / START_EQ - 1) * 100:+.1f}%"
        exp_wr, exp_r = EXPECT.get(sid, (50, "—"))
        lines.append(f"{medals[i]} <b>{sid}</b> {name}")
        lines.append(f"    净值 <b>${eq:,.0f}</b>（{tag}）· 本周 {wk:+,.0f}$")
        if st["n"]:
            mw, ml = streaks(sid)
            lines.append(f"    {st['n']}单 {st['wins']}胜{st['losses']}败 "
                         f"胜率{st['wr'] * 100:.0f}% · 连胜{mw}/连败{ml}")
            if st["n"] >= 8:
                ok = abs(st["wr"] * 100 - exp_wr) <= 15
                lines.append(f"    vs 回测预期 胜率{exp_wr}% 均{exp_r}: "
                             f"{'✅ 符合' if ok else '⚠️ 偏离'}")
            else:
                lines.append(f"    vs 回测预期 胜率{exp_wr}% 均{exp_r}（样本不足8笔暂不评估）")
        else:
            lines.append(f"    尚无成交 · 回测预期 胜率{exp_wr}% 均{exp_r}")
    lines.append("")
    # E层执行质量（近7日）
    if os.path.exists(EXEC_F):
        ev = {"SHADOW_HALT_DAYR": 0, "SHADOW_HALT_STREAK": 0, "BACKFILL_ENTRY": 0, "DATA_SKIP": 0}
        for r in csv.DictReader(open(EXEC_F)):
            if now_ts - int(r["ts"]) <= 7 * 86400 and r["event"] in ev:
                ev[r["event"]] += 1
        lines.append(f"🔧 执行质量: 影子熔断{ev['SHADOW_HALT_DAYR'] + ev['SHADOW_HALT_STREAK']}次 · "
                     f"补检单{ev['BACKFILL_ENTRY']}笔 · 数据跳过{ev['DATA_SKIP']}次")
        lines.append("")
    lines.append("<i>📋 纸面赛马 — 非实盘。样本≥30笔且符合预期者获实盘候选资格。</i>")
    if tgx.send_message("\n".join(lines)):
        state["week_reported"] = week_key
    else:
        print("[WEEKLY FAIL] 发送失败，下小时重试")


def demo():
    """发送开单+关单图文范例（用真实BTC K线）"""
    bars = fetch_bars("BTCUSDT", 200)
    if len(bars) < 60:
        print("demo: 数据不足")
        return
    t_out = bars[-3]["ts"]
    t_in = bars[-33]["ts"]
    entry = bars[-33]["close"]
    stop = entry * 1.0138
    tp = entry - 1.5 * (stop - entry)
    exit_px = tp
    pnl_pct = (entry - exit_px) / entry - 2 * COST_SIDE
    sig = {"no": 0, "direction": "SHORT", "t_entry": t_in, "t_exit": t_out,
           "entry": entry, "stop": stop, "tp": tp, "exit": exit_px,
           "r_mult": pnl_pct / 0.0138}
    tgx.send_message("【演示】" + tgx.msg_open_race(
        "S10", "阻力衰竭空·MA7", 0, "BTC", "SHORT", entry, stop, tp, START_EQ,
        "距阻力1.38% · CVD -495 · FR +0.0067% · MA7下方"))
    caption = "【演示】" + tgx.caption_close_race(
        "S10", "阻力衰竭空·MA7", "BTC", "tp", sig, pnl_pct * 100,
        NOTIONAL * pnl_pct, sig["r_mult"], START_EQ + NOTIONAL * pnl_pct,
        {"wins": 1, "losses": 0, "n": 1, "wr": 1.0, "total_usd": NOTIONAL * pnl_pct})
    import chart
    png = chart.render_close_chart("DPL-Flow", 0, "S10", sig, bars)
    ok = tgx.send_photo(png, caption)
    print(f"demo 发送: {'OK' if ok else 'FAIL'}")


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "demo":
        demo()
        return
    if len(sys.argv) > 1 and sys.argv[1] == "report":
        state = json.load(open(STATE_F)) if os.path.exists(STATE_F) else {}
        weekly_report(state, int(time.time()), force=True)
        json.dump(state, open(STATE_F, "w"), indent=2)
        print("补发周报完成")
        return
    state = json.load(open(STATE_F)) if os.path.exists(STATE_F) else {}
    # E层护栏1：账本自洽校验（净值 = 本金 + Σ已平仓盈亏）
    for sid in STRATS:
        ss = state.get(sid)
        if ss and abs(ss.get("equity", START_EQ) - (START_EQ + strat_stats(sid)["total_usd"])) > 0.05:
            log_exec("LEDGER_MISMATCH", sid, ss.get("equity"))
            tgx.send_message(f"⚠️ <b>{tgx.BRAND}</b> E层告警：{sid} 账本不自洽，本轮暂停开单，请人工核对\n<i>📋 纸面赛马 — 非实盘</i>")
            return
    feats, raw = {}, {}
    btc_fund = fetch_funding("BTCUSDT")
    for sym, pair in (("BTC", "BTCUSDT"), ("ETH", "ETHUSDT")):
        bars, tk = fetch_bars(pair), fetch_taker(pair)
        # E层护栏2：数据守门（行数/跳变/流数据缺失）
        bad = ""
        if len(bars) < 400:
            bad = f"bars={len(bars)}"
        elif len(bars) >= 2 and abs(bars[-1]["close"] / bars[-2]["close"] - 1) > 0.2:
            bad = "价格跳变>20%"
        elif not tk:
            bad = "taker空"
        if bad:
            log_exec("DATA_SKIP", sym, bad)
            tgx.send_message(f"⚠️ <b>{tgx.BRAND}</b> E层告警：{sym} 数据异常({bad})，本轮跳过该品种\n<i>📋 纸面赛马 — 非实盘</i>")
            feats[sym] = None
            raw[sym] = (bars, tk)
            continue
        raw[sym] = (bars, tk)
        f = features(bars, tk, btc_fund)
        feats[sym] = f
        if f:
            print(f"{sym} bar={datetime.fromtimestamp(f['t0'], tz=timezone.utc)} "
                  f"close={f['close']:.0f} cvd={f['cvd24']:.0f} rsi4h={f['rsi4h'] and round(f['rsi4h'], 1)}")
    for sid, cfg in STRATS.items():
        run_strategy(sid, cfg, feats, state, raw, btc_fund)
    if feats.get("BTC"):
        weekly_report(state, feats["BTC"]["t0"])
    json.dump(state, open(STATE_F, "w"), indent=2)
    print("state 已保存")


if __name__ == "__main__":
    main()
