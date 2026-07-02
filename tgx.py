#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""大漂亮资金流秘籍 — TG 推送（赛马版模板）"""
import html as html_module
import json
import os
import urllib.request

BRAND = "大漂亮资金流秘籍"


def _esc(s):
    return html_module.escape(str(s), quote=False)


def _cfg():
    return os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")


def send_message(text, parse_mode="HTML"):
    tok, cid = _cfg()
    if not tok or not cid:
        print(f"[TG WARN] 未配置，转stdout:\n{text}\n")
        return False
    payload = json.dumps({"chat_id": cid, "text": text, "parse_mode": parse_mode,
                          "disable_web_page_preview": True}).encode()
    req = urllib.request.Request(f"https://api.telegram.org/bot{tok}/sendMessage",
                                 data=payload, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read().decode()).get("ok", False)
    except Exception as e:
        print(f"[TG FAIL] {e}")
        return False


def send_photo(photo_bytes, caption, parse_mode="HTML"):
    tok, cid = _cfg()
    if not tok or not cid:
        print(f"[TG WARN] 未配置，caption转stdout:\n{caption}\n")
        return False
    boundary = "----dplBoundary9f2c1e"
    parts = []
    for k, v in (("chat_id", cid), ("caption", caption[:1024]), ("parse_mode", parse_mode)):
        parts.append(f"--{boundary}\r\nContent-Disposition: form-data; "
                     f"name=\"{k}\"\r\n\r\n{v}\r\n".encode())
    parts.append((f"--{boundary}\r\nContent-Disposition: form-data; name=\"photo\"; "
                  f"filename=\"report.png\"\r\nContent-Type: image/png\r\n\r\n").encode()
                 + photo_bytes + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    body = b"".join(parts)
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{tok}/sendPhoto", data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}",
                 "Content-Length": str(len(body))})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode()).get("ok", False)
    except Exception as e:
        print(f"[TG PHOTO FAIL] {e}")
        return False


def msg_open_race(sid, name, no, sym, direction, entry, sl, tp, equity, cond_desc):
    d = "📉 做空" if direction == "SHORT" else "📈 做多"
    sl_pct = abs(sl - entry) / entry * 100
    tp_pct = abs(tp - entry) / entry * 100
    return (
        f"💅 <b>{BRAND}</b> · 策略赛马\n"
        f"🔔 <b>{sid} 开单 #{no:03d}</b> — {_esc(name)}\n"
        f"━━━━━━━━━━━━━━━\n"
        f"品种: <b>{_esc(sym)}</b> | {d}\n"
        f"触发: {_esc(cond_desc)}\n\n"
        f"📍 入场: <code>${entry:,.2f}</code>\n"
        f"🛑 止损: <code>${sl:,.2f}</code> ({sl_pct:.2f}%)\n"
        f"🎯 止盈: <code>${tp:,.2f}</code> ({tp_pct:.2f}%, 1.5R)\n\n"
        f"⚙️ 仓位: 保证金 $1,000 × 10x = 名义 $10,000\n"
        f"💰 该策略当前净值: <b>${equity:,.0f}</b>\n\n"
        f"<i>📋 纸面赛马 — 非实盘指令</i>"
    )


def caption_close_race(sid, name, sym, reason, sig, pnl_pct, pnl_usd, r_mult, equity, st):
    from datetime import datetime, timezone
    fmt = lambda ts: datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%m-%d %H:%M")
    how = {"tp": "🎯 打到 1.5R 目标价，止盈出场",
           "stop": "🛑 打到 L01 止损位出场",
           "time": "⏱ 72h 到期，收盘价强平"}[reason]
    hold_h = (sig["t_exit"] - sig["t_entry"]) / 3600
    short = sig["direction"] == "SHORT"
    return (
        f"💅 <b>{BRAND}</b> · 策略赛马\n"
        f"🧾 <b>{sid} 关单 #{sig.get('no', 0):03d} 复盘</b> — {_esc(name)} {_esc(sym)}\n"
        f"{'📉 空单' if short else '📈 多单'} · $1,000×10x\n"
        f"━━━━━━━━━━━━━━━\n"
        f"✅ {fmt(sig['t_entry'])} 进场 <code>${sig['entry']:,.0f}</code>"
        f" (SL ${sig['stop']:,.0f} / TP ${sig['tp']:,.0f})\n"
        f"{how}\n"
        f"🏁 {fmt(sig['t_exit'])} 出场 <code>${sig['exit']:,.0f}</code>\n\n"
        f"盈亏: <b>{pnl_usd:+,.0f}$</b>（{r_mult:+.2f}R / {pnl_pct:+.2f}%）"
        f"{'✅' if pnl_usd > 0 else '❌'} · 持仓 {hold_h:.1f}h\n"
        f"💰 净值: <b>${equity:,.0f}</b>（{(equity / 10000 - 1) * 100:+.1f}%）\n"
        f"📊 {st['n']}单 {st['wins']}胜{st['losses']}败 胜率{st['wr'] * 100:.0f}%"
        f" 累计{st['total_usd']:+,.0f}$\n"
        f"<i>📋 纸面赛马 — 非实盘</i>"
    )
