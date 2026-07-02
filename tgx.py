#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""大漂亮资金流秘籍 — TG 推送（文字 + 图文，样式对齐 F6）"""
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


def msg_open(no, strat, sym, direction, entry, sl, tp, lev, cond_desc):
    d = "📉 做空" if direction == "SHORT" else "📈 做多"
    side = "Short" if direction == "SHORT" else "Long"
    sl_pct = abs(sl - entry) / entry * 100
    tp_pct = abs(tp - entry) / entry * 100
    return (
        f"💅 <b>{BRAND}</b>\n"
        f"🔔 <b>新信号 #{no:03d}</b> — {_esc(strat)}\n"
        f"━━━━━━━━━━━━━━━\n"
        f"品种: <b>{_esc(sym)}</b>\n"
        f"方向: {d} ({side})\n"
        f"触发: {_esc(cond_desc)}\n\n"
        f"📍 <b>入场</b>: <code>${entry:,.2f}</code> (信号K线收盘市价)\n"
        f"🛑 <b>止损</b>: <code>${sl:,.2f}</code> ({sl_pct:.2f}%, L01结构位)\n"
        f"🎯 <b>止盈</b>: <code>${tp:,.2f}</code> ({tp_pct:.2f}%, 1.5R单目标)\n\n"
        f"⚙️ 仓位: 名义杠杆 <b>{lev:.2f}x</b>\n"
        f"⏱ 最长持仓 72h，超时强平\n\n"
        f"<i>📋 纸面验证模式 — 非实盘指令</i>"
    )


def caption_close(no, strat, sym, reason, sig, pnl_pct, r_mult, stats):
    """关单图的 caption（时间线复盘，对齐 F6 风格）"""
    from datetime import datetime, timezone
    fmt = lambda ts: datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%m-%d %H:%M")
    how = {"tp": "🎯 打到 1.5R 目标价，止盈出场",
           "stop": "🛑 打到 L01 止损位出场",
           "time": "⏱ 72h 到期，收盘价强平"}[reason]
    hold_h = (sig["t_exit"] - sig["t_entry"]) / 3600
    short = sig["direction"] == "SHORT"
    return (
        f"💅 <b>{BRAND}</b>\n"
        f"🧾 <b>#{no:03d} 关单复盘</b> — {_esc(strat)} {_esc(sym)}\n"
        f"{'📉 空单' if short else '📈 多单'} · 杠杆 {sig.get('lev', 1.0):.2f}×\n"
        f"━━━━━━━━━━━━━━━\n"
        f"✅ {fmt(sig['t_entry'])} 进场 <code>${sig['entry']:,.0f}</code>"
        f" (SL ${sig['stop']:,.0f} / TP ${sig['tp']:,.0f})\n"
        f"{how}\n"
        f"🏁 {fmt(sig['t_exit'])} 出场 <code>${sig['exit']:,.0f}</code>\n\n"
        f"盈亏: <b>{r_mult:+.2f}R</b>（净 {pnl_pct:+.2f}%）{'✅' if pnl_pct > 0 else '❌'}"
        f" · 持仓 {hold_h:.1f}h\n"
        f"📊 战绩: {stats['wins']}胜{stats['losses']}败 / 胜率 {stats['wr']*100:.0f}%"
        f" / 累计 <b>{stats['total_r']:+.2f}R</b>（{stats['total_pct']:+.2f}%）\n"
        f"<i>📋 纸面验证 vs 回测: 胜率{stats['expect_wr']} 均R{stats['expect_r']}</i>"
    )
