"""
📡 Radar — رادار الصاعدين المبكر (تنبيهات + تتبع افتراضي، بدون أي شراء)
========================================================================
1) كل 5 دقايق: يفحص تغيّر 24 ساعة لكل العملات (طلب واحد) ويختار اللي بين +2% و+6% وسيولتها كافية.
2) للمرشحين: analyze_fn (من البوت) يفحص قفزة الفوليوم والمتوسطات وكسر القمة وبوابة مخاطر أخف.
3) لو نجحت: تنبيه على تيليغرام + تسجيل "صفقة افتراضية" بسعر التنبيه.
4) التتبع: كل دقيقة نمرّ على شموع الدقيقة ونطبّق قواعد خروج البوت الحقيقية بالضبط (الدوال بتنحقن من
   البوت: الستوب الأولي ATR مع السقف الصلب، نقطة تفعيل الـ Trailing، ستوب الـ Trailing مع قفل الحد
   الأدنى للربح). النتائج:
     - ضرب الستوب قبل التفعيل  → 🔴 خسارة
     - تفعّل الـ Trailing        → ✅ "نجحت" (تنبيه) ثم 💰 النتيجة النهائية لما يخرج بالـ Trailing
     - مرت 24 ساعة بدون حسم     → ⏱️ تنتهي بنسبتها الحالية
5) "الذاكرة": كل تنبيه ونتيجته محفوظ بملف JSON، والأمر /radar_report بيطلّع الإحصائيات العامة.

⚠️ المحاكاة تقريبية: شموع دقيقة (مو كل tick)، والخروج بسعر الستوب بدون انزلاق، وبنطرح رسوم
تقديرية (RADAR_FEE_PCT لكل جهة). عند تضارب الستوب والتفعيل بنفس الشمعة نفترض الستوب أول (متحفظ).
مستقل عن البوت: كل الاعتماديات بتنحقن بالـ constructor.
"""

import json
import logging
import os
import threading
import time

log = logging.getLogger(__name__)

# ── الإعدادات (تقديرية، عدّلها هون) ──
SCAN_EVERY_SECONDS = 300
TRACK_EVERY_SECONDS = 60
FIRST_DELAY_SECONDS = 90
MIN_CHANGE_24H = 2.0
MAX_CHANGE_24H = 6.0
MIN_QUOTE_VOLUME = 2_000_000
MAX_CANDIDATES_PER_SCAN = 20
ALERT_COOLDOWN_SECONDS = 6 * 3600
TRACK_TIMEOUT_SECONDS = 24 * 3600
MAX_OPEN_ALERTS = 15
RADAR_FEE_PCT = 0.1            # رسوم تقديرية لكل جهة (شراء/بيع) بنسبة مئوية
KEEP_DAYS = 60
PER_SYMBOL_SLEEP = 0.4


def simulate(alert, candles, initial_stop_fn, trigger_fn, trail_stop_fn):
    """
    يطبّق قواعد خروج البوت على شموع الدقيقة المرتبة زمنياً. يعدّل alert مباشرة ويرجع قائمة أحداث:
    [("activated", price), ("closed", price, outcome)].
    candles: [(high, low, close)] — شموع مغلقة فقط.
    """
    events = []
    entry, atr = alert["entry"], alert.get("atr")
    for high, low, close in candles:
        alert["last_close"] = close
        if alert["state"] == "open":
            if low <= alert["stop"]:
                alert.update(state="closed", outcome="stop", exit_price=alert["stop"])
                events.append(("closed", alert["stop"], "stop"))
                break
            if high >= alert["trigger"]:
                alert["state"] = "trailing"
                alert["highest"] = max(alert["trigger"], high)
                alert["stop"] = trail_stop_fn(alert["highest"], entry, atr)
                events.append(("activated", alert["trigger"]))
        elif alert["state"] == "trailing":
            if low <= alert["stop"]:
                alert.update(state="closed", outcome="trailing", exit_price=alert["stop"])
                events.append(("closed", alert["stop"], "trailing"))
                break
            if high > alert["highest"]:
                alert["highest"] = high
                alert["stop"] = trail_stop_fn(high, entry, atr)
    return events


def _pct(entry, price):
    return (price - entry) / entry * 100


class Radar:
    def __init__(self, path, symbols_fn, exclude_fn, tickers_fn, analyze_fn, klines_fn,
                 initial_stop_fn, trigger_fn, trail_stop_fn, notify_fn,
                 blocked_fn, enabled_fn=lambda: True, amount_fn=lambda: 15.0):
        """
        tickers_fn() -> list[{"symbol","lastPrice","priceChangePercent","quoteVolume"}]
        analyze_fn(symbol) -> {"ok": bool, "reason": str, "ind": dict, "vol_ratio": float, "notes": [str]}
        klines_fn(symbol, start_ms) -> شموع دقيقة خام بصيغة بينانس (openTime, o, h, l, c, v, closeTime, ...)
        initial_stop_fn(entry, atr) / trigger_fn(entry, atr) / trail_stop_fn(price, entry, atr) -> float
        """
        self.path = path
        self.symbols_fn, self.exclude_fn = symbols_fn, exclude_fn
        self.tickers_fn, self.analyze_fn, self.klines_fn = tickers_fn, analyze_fn, klines_fn
        self.initial_stop_fn, self.trigger_fn, self.trail_stop_fn = initial_stop_fn, trigger_fn, trail_stop_fn
        self.notify, self.blocked_fn, self.enabled_fn, self.amount_fn = notify_fn, blocked_fn, enabled_fn, amount_fn
        self._lock = threading.Lock()
        self.alerts = self._load()
        self._last_scan = 0

    # ── تخزين ("الذاكرة") ──
    def _load(self):
        try:
            if os.path.exists(self.path):
                with open(self.path, "r", encoding="utf-8") as f:
                    return json.load(f).get("alerts", [])
        except Exception as e:
            log.error(f"❌ Radar: تعذر قراءة السجل: {e}")
        return []

    def _save(self):
        try:
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"alerts": self.alerts}, f, ensure_ascii=False)
            os.replace(tmp, self.path)
        except Exception as e:
            log.error(f"❌ Radar: تعذر حفظ السجل: {e}")

    def _say(self, text):
        try:
            self.notify(text)
        except Exception as e:
            log.error(f"❌ Radar notify: {e}")

    # ── المسح ──
    def scan_once(self):
        if self.blocked_fn():
            return
        now = time.time()
        with self._lock:
            n_open = sum(1 for a in self.alerts if a["state"] != "closed")
            busy = {a["symbol"] for a in self.alerts if a["state"] != "closed"}
            cooling = {a["symbol"] for a in self.alerts if now - a["ts"] < ALERT_COOLDOWN_SECONDS}
        if n_open >= MAX_OPEN_ALERTS:
            return
        allowed = set(self.symbols_fn()) - set(self.exclude_fn()) - busy - cooling
        try:
            tickers = self.tickers_fn()
        except Exception as e:
            log.error(f"❌ Radar: تعذر جلب الأسعار: {e}")
            return
        pool = []
        for t in tickers:
            sym = t.get("symbol")
            if sym not in allowed:
                continue
            try:
                ch, qv, price = float(t["priceChangePercent"]), float(t["quoteVolume"]), float(t["lastPrice"])
            except (KeyError, ValueError, TypeError):
                continue
            if MIN_CHANGE_24H <= ch <= MAX_CHANGE_24H and qv >= MIN_QUOTE_VOLUME and price > 0:
                pool.append((qv, sym, ch, price))
        pool.sort(reverse=True)
        found = 0
        for qv, sym, ch, price in pool[:MAX_CANDIDATES_PER_SCAN]:
            if self.blocked_fn():
                break
            try:
                res = self.analyze_fn(sym)
            except Exception as e:
                log.error(f"❌ Radar analyze {sym}: {e}")
                continue
            time.sleep(PER_SYMBOL_SLEEP)
            if not res or not res.get("ok"):
                continue
            self._open_alert(sym, price, ch, res)
            found += 1
            with self._lock:
                if sum(1 for a in self.alerts if a["state"] != "closed") >= MAX_OPEN_ALERTS:
                    break
        log.info(f"📡 Radar: مرشحين بعد فلتر +{MIN_CHANGE_24H}%..+{MAX_CHANGE_24H}%: {len(pool)} | تنبيهات جديدة: {found}")

    def _open_alert(self, sym, price, change24, res):
        ind = res.get("ind") or {}
        atr = ind.get("atr")
        stop = self.initial_stop_fn(price, atr)
        trigger = self.trigger_fn(price, atr)
        now = time.time()
        alert = {
            "id": f"{int(now)}-{sym}", "symbol": sym, "ts": now, "entry": price, "atr": atr,
            "change24": round(change24, 2), "vol_ratio": round(res.get("vol_ratio") or 0, 2),
            "stop": stop, "trigger": trigger, "initial_stop": stop, "highest": price,
            "state": "open", "last_ms": int(now * 1000), "last_close": price,
            "outcome": None, "exit_price": None, "closed_ts": None, "activated_ts": None,
        }
        with self._lock:
            self.alerts.append(alert)
            self._save()
        coin = sym.replace("USDT", "")
        lines = [
            f"📡 <b>رادار: {coin} بلشت تطلع</b>  (تنبيه فقط — ما تم أي شراء)",
            f"💰 السعر: {price:g} | تغيّر 24س: {change24:+.1f}% | فوليوم: {alert['vol_ratio']}× المتوسط",
        ]
        for n in res.get("notes", []):
            lines.append(f"• {n}")
        if ind.get("vp_stop_hint") is not None and ind.get("vp_target_hint") is not None:
            lines.append(f"📐 أقرب دعم حجمي: {ind['vp_stop_hint']:g} | أقرب هدف حجمي: {ind['vp_target_hint']:g}")
        lines.append(
            f"🧪 تتبع افتراضي بقواعد البوت: ستوب {stop:g} ({_pct(price, stop):+.1f}%) | "
            f"تفعيل Trailing عند {trigger:g} ({_pct(price, trigger):+.1f}%)"
        )
        self._say("\n".join(lines))

    # ── التتبع ──
    def track_once(self):
        now = time.time()
        with self._lock:
            live = [a for a in self.alerts if a["state"] != "closed"]
        for a in live:
            try:
                raw = self.klines_fn(a["symbol"], a["last_ms"] + 1)
            except Exception as e:
                log.error(f"❌ Radar klines {a['symbol']}: {e}")
                continue
            now_ms = int(now * 1000)
            candles, last_close_ms = [], None
            for k in raw or []:
                if int(k[6]) <= now_ms:   # شمعة مغلقة فقط
                    candles.append((float(k[2]), float(k[3]), float(k[4])))
                    last_close_ms = int(k[6])
            with self._lock:
                if last_close_ms:
                    a["last_ms"] = last_close_ms
                events = simulate(a, candles, self.initial_stop_fn, self.trigger_fn, self.trail_stop_fn)
                timed_out = a["state"] != "closed" and now - a["ts"] >= TRACK_TIMEOUT_SECONDS
                if timed_out:
                    a.update(state="closed", outcome="timeout", exit_price=a["last_close"])
                    events.append(("closed", a["last_close"], "timeout"))
                if a["state"] == "closed":
                    a["closed_ts"] = now
                if any(e[0] == "activated" for e in events):
                    a["activated_ts"] = now
                self._save()
            for ev in events:
                self._announce(a, ev)

    def _net(self, a):
        gross = _pct(a["entry"], a["exit_price"])
        return gross, gross - 2 * RADAR_FEE_PCT

    def _announce(self, a, ev):
        coin = a["symbol"].replace("USDT", "")
        if ev[0] == "activated":
            self._say(
                f"✅ <b>{coin} نجحت</b>: وصلت {_pct(a['entry'], ev[1]):+.1f}% من سعر التنبيه وتفعّل الـ Trailing "
                f"(الستوب صار {a['stop']:g}، يعني ربح مقفول على الأقل)."
            )
            return
        _, price, outcome = ev
        gross, net = self._net(a)
        hours = (time.time() - a["ts"]) / 3600
        if outcome == "stop":
            self._say(f"🔴 <b>{coin} خسرت</b>: ضربت الستوب بعد {hours:.1f} ساعة — {gross:+.2f}% (صافي بعد الرسوم {net:+.2f}%).")
        elif outcome == "trailing":
            self._say(f"💰 <b>{coin} خرجت بالـ Trailing</b> بعد {hours:.1f} ساعة — {gross:+.2f}% (صافي بعد الرسوم {net:+.2f}%).")
        else:
            state_txt = "والـ Trailing لسا شغال" if a.get("activated_ts") else "بدون ما توصل لا الستوب ولا نقطة التفعيل"
            self._say(f"⏱️ <b>{coin}</b>: مرت 24 ساعة {state_txt} — الحالة {gross:+.2f}% (صافي {net:+.2f}%).")

    # ── التقرير العام ──
    def report(self):
        with self._lock:
            alerts = [dict(a) for a in self.alerts]
        if not alerts:
            return "📡 <b>تقرير الرادار</b>\nما في تنبيهات لسا."
        closed = [a for a in alerts if a["state"] == "closed"]
        live = [a for a in alerts if a["state"] != "closed"]
        lines = [f"📡 <b>تقرير الرادار</b>\nتنبيهات: {len(alerts)} | محسومة: {len(closed)} | قيد التتبع: {len(live)}"]
        if closed:
            nets = [self._net(a)[1] for a in closed]
            wins = [n for n in nets if n > 0]
            avg = sum(nets) / len(nets)
            by = {"stop": 0, "trailing": 0, "timeout": 0}
            for a in closed:
                by[a["outcome"]] = by.get(a["outcome"], 0) + 1
            activated = sum(1 for a in alerts if a.get("activated_ts"))
            gain = sum(n for n in nets if n > 0)
            loss = -sum(n for n in nets if n < 0)
            pf = f"{gain / loss:.2f}" if loss > 0 else "—"
            amt = self.amount_fn()
            usd = sum(nets) * amt / 100
            best = max(closed, key=lambda a: self._net(a)[1])
            worst = min(closed, key=lambda a: self._net(a)[1])
            lines += [
                f"\n<b>النتائج (صافي بعد رسوم {2 * RADAR_FEE_PCT:.1f}%):</b>",
                f"• متوسط النتيجة لكل تنبيه: {avg:+.2f}%",
                f"• رابحة: {len(wins)}/{len(closed)} ({len(wins) / len(closed) * 100:.0f}%) | معامل الربح: {pf}",
                f"• ضربت ستوب: {by['stop']} | خرجت بـ Trailing: {by['trailing']} | انتهت بدون حسم: {by['timeout']}",
                f"• فعّلت Trailing (نجحت): {activated}/{len(alerts)}",
                f"• لو اشتريت {amt:g}$ بكل تنبيه: {usd:+.2f}$",
                f"• أفضل: {best['symbol'].replace('USDT', '')} {self._net(best)[1]:+.2f}% | أسوأ: {worst['symbol'].replace('USDT', '')} {self._net(worst)[1]:+.2f}%",
            ]
        if live:
            lines.append("\n<b>قيد التتبع:</b>")
            for a in live[-5:]:
                cur = _pct(a["entry"], a["last_close"])
                st = "Trailing مفعّل" if a["state"] == "trailing" else "بانتظار"
                lines.append(f"• {a['symbol'].replace('USDT', '')}: {cur:+.2f}% ({st})")
        if len(closed) < 30:
            lines.append(f"\n⚠️ العينة صغيرة ({len(closed)} محسومة، المطلوب حوالي 30 على الأقل) — الأرقام غير حاسمة بعد.")
        return "\n".join(lines)

    def _prune(self):
        cutoff = time.time() - KEEP_DAYS * 86400
        with self._lock:
            before = len(self.alerts)
            self.alerts = [a for a in self.alerts if a["state"] != "closed" or a["ts"] >= cutoff]
            if len(self.alerts) != before:
                self._save()

    def run(self):
        time.sleep(FIRST_DELAY_SECONDS)
        while True:
            try:
                if self.enabled_fn():
                    now = time.time()
                    if now - self._last_scan >= SCAN_EVERY_SECONDS:
                        self._last_scan = now
                        self.scan_once()
                self.track_once()   # التتبع شغال حتى لو الرادار مطفي (ننهي اللي بدأناه)
                self._prune()
            except Exception as e:
                log.error(f"❌ Radar: خطأ بالحلقة: {e}")
            time.sleep(TRACK_EVERY_SECONDS)
