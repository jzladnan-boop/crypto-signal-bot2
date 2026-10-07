"""
🎯 Agent Scanner — ماسح فرص الوكيل (توصيات فقط — بدون أي شراء)
================================================================
يفحص كل عملات القائمة كل ساعة بثلاث طبقات:

1) الكود (قواعد ثابتة): استبعاد العملات الخطرة (سيولة ضعيفة، تمدد فوق VAH،
   Supertrend هابط، ADX ضعيف، MFI متطرف...) وإعطاء كل عملة نقاط من 0 إلى 11.
2) الوكيل (Gemini): يقارن أفضل 5 مرشحين ويختار وحدة أو يرفض الكل مع السبب.
3) التسجيل والقياس: كل توصية (وكل مسح) بتنسجل بسعرها، وبعد 4 و24 ساعة
   بنحسب النتيجة ونقارنها مع متوسط السوق ومع متوسط أفضل 5 مرشحين — عشان
   نعرف بالأرقام إذا الوكيل فعلاً بيضيف قيمة قبل ما نسمح له يشتري.

⚠️ أوزان النقاط هي نقطة بداية منطقية (مش مضبوطة على بيانات) — التقرير
(/agent_report) هو اللي بيقول إذا بتشتغل أو لازم تتعدل.

مستقل عن البوت الرئيسي: ما يستورد منه شي، كل الاعتماديات تنحقن بالـ constructor.
"""

import json
import os
import threading
import time
import logging

import pandas as pd

import ai_agent

log = logging.getLogger(__name__)

# ── إعدادات ──
SCAN_EVERY_SECONDS = 3600          # مسح كل ساعة
FIRST_SCAN_DELAY = 120             # تأخير أول مسح بعد تشغيل البوت
PER_SYMBOL_SLEEP = 0.3             # حماية من حظر -1003
MIN_QUOTE_VOLUME_24H = 2_000_000   # سيولة دنيا (USDT/24س) — للتوصيات فقط
MIN_SCORE = 6                      # أقل نقاط لدخول قائمة المرشحين
PRE_TOP_N = 8                      # عدد المرشحين اللي ياخدوا فحص فريم 4 ساعات
FINAL_TOP_N = 5                    # عدد المرشحين اللي بيوصلوا للوكيل
RECOMMEND_COOLDOWN_SECONDS = 6 * 3600   # ما نكرر توصية نفس العملة قبل 6 ساعات
KEEP_DAYS = 14
HORIZONS = (("r4", 4 * 3600), ("r24", 24 * 3600))


class AgentScanner:
    def __init__(self, client, symbols_fn, indicators_fn, regime_fn, notify_fn,
                 blocked_fn, exclude_fn, data_dir, enabled_fn=lambda: True):
        self.client = client
        self.symbols_fn = symbols_fn
        self.indicators_fn = indicators_fn
        self.regime_fn = regime_fn
        self.notify = notify_fn
        self.blocked_fn = blocked_fn
        self.exclude_fn = exclude_fn
        self.enabled_fn = enabled_fn
        self.path = os.path.join(data_dir, "agent_scan_log.json")
        self._lock = threading.Lock()
        self.scans = self._load()
        self.last_scan = 0

    # ──────────────────────────────────────────────
    # تخزين
    # ──────────────────────────────────────────────
    def _load(self):
        try:
            if os.path.exists(self.path):
                with open(self.path, "r", encoding="utf-8") as f:
                    return json.load(f).get("scans", [])
        except Exception as e:
            log.error(f"❌ AgentScanner: تعذر قراءة السجل: {e}")
        return []

    def _save(self):
        try:
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"scans": self.scans}, f, ensure_ascii=False)
            os.replace(tmp, self.path)
        except Exception as e:
            log.error(f"❌ AgentScanner: تعذر حفظ السجل: {e}")

    # ──────────────────────────────────────────────
    # التقييم بالنقاط (قواعد ثابتة)
    # ──────────────────────────────────────────────
    @staticmethod
    def score(ind, quote_volume):
        """يرجع (score, flags) أو (None, سبب_الاستبعاد)"""
        price = ind.get("price")
        if price is None:
            return None, "no_price"
        if quote_volume is None or quote_volume < MIN_QUOTE_VOLUME_24H:
            return None, "low_liquidity"
        if ind.get("supertrend_direction") == "down":
            return None, "supertrend_down"
        if ind.get("vp_position") == "above_vah":
            return None, "extended_above_vah"
        adx, mfi = ind.get("adx"), ind.get("mfi")
        if adx is None or adx < 20:
            return None, "weak_adx"
        if mfi is not None and mfi > 80:
            return None, "overbought_mfi"
        vol, vol_mean = ind.get("volume"), ind.get("volume_mean")
        vol_ratio = (vol / vol_mean) if vol and vol_mean else None
        if vol_ratio is None or vol_ratio < 1.0:
            return None, "weak_volume"

        pts, flags = 0, []
        if ind.get("supertrend_direction") == "up":
            pts += 2; flags.append("supertrend_up")
        sma50 = ind.get("sma50")
        if sma50 and price > sma50:
            pts += 1; flags.append("above_sma50")
        if adx >= 25:
            pts += 2; flags.append("adx_strong")
        else:
            pts += 1; flags.append("adx_ok")
        if vol_ratio >= 1.5:
            pts += 2; flags.append("volume_surge")
        else:
            pts += 1; flags.append("volume_ok")
        if mfi is not None and 40 <= mfi <= 65:
            pts += 1; flags.append("mfi_balanced")
        pos = ind.get("vp_position")
        if pos == "val_to_poc":
            pts += 2; flags.append("near_value_support")
        elif pos == "poc_to_vah":
            pts += 1; flags.append("inside_value_area")
        return pts, flags

    # ──────────────────────────────────────────────
    # فريم 4 ساعات (للمرشحين النهائيين بس — توفير طلبات)
    # ──────────────────────────────────────────────
    def _htf_up(self, symbol):
        try:
            kl = self.client.get_klines(symbol=symbol, interval="4h", limit=60)
            closes = pd.Series([float(k[4]) for k in kl])
            if len(closes) < 50:
                return None
            return bool(closes.iloc[-1] > closes.rolling(50).mean().iloc[-1])
        except Exception:
            return None

    # ──────────────────────────────────────────────
    # مسح واحد
    # ──────────────────────────────────────────────
    def scan_once(self):
        if self.blocked_fn():
            return
        symbols = [s for s in self.symbols_fn() if s not in self.exclude_fn()]
        if not symbols:
            return
        try:
            prices_all = {t["symbol"]: float(t["price"]) for t in self.client.get_all_tickers()}
            vols = {t["symbol"]: float(t["quoteVolume"]) for t in self.client.get_ticker()}
        except Exception as e:
            log.error(f"❌ AgentScanner: تعذر جلب الأسعار/الأحجام: {e}")
            return

        scanned_prices = {}
        scored = []
        excluded = {}
        for sym in symbols:
            if self.blocked_fn():
                log.warning("🚦 AgentScanner: توقف المسح — حظر مؤقت")
                return
            ind = self.indicators_fn(self.client, sym)
            time.sleep(PER_SYMBOL_SLEEP)
            if not ind:
                continue
            if sym in prices_all:
                scanned_prices[sym] = prices_all[sym]
            pts, info = self.score(ind, vols.get(sym))
            if pts is None:
                excluded[info] = excluded.get(info, 0) + 1
                continue
            if pts >= MIN_SCORE:
                scored.append((pts, sym, ind, info))

        scored.sort(key=lambda x: x[0], reverse=True)
        pre = scored[:PRE_TOP_N]

        # فريم 4 ساعات للمرشحين + نقطة إضافية
        enriched = []
        for pts, sym, ind, flags in pre:
            htf = self._htf_up(sym)
            time.sleep(PER_SYMBOL_SLEEP)
            total = pts + (1 if htf else 0)
            enriched.append((total, sym, ind, flags, htf))
        enriched.sort(key=lambda x: x[0], reverse=True)
        top = enriched[:FINAL_TOP_N]

        regime = self.regime_fn()
        payload = []
        for total, sym, ind, flags, htf in top:
            payload.append({
                "symbol": sym, "score": total, "flags": flags,
                "btc_regime": regime, "trend_4h_above_sma50": htf,
                "quote_volume_24h": round(vols.get(sym, 0)),
                **{k: ind.get(k) for k in (
                    "price", "rsi", "mfi", "adx", "volume", "volume_mean", "momentum_score",
                    "supertrend_direction", "bb_upper", "bb_lower", "vwap",
                    "vp_poc", "vp_val", "vp_vah", "vp_position", "vp_stop_hint", "vp_target_hint")},
            })

        pick, reason, source = None, "لا يوجد مرشحين اجتازوا القواعد حالياً", "rules"
        if payload:
            verdict = ai_agent.compare_candidates(payload)
            source = verdict.get("source", "")
            reason = verdict.get("reason_ar", "")
            if verdict.get("approved") and verdict.get("symbol"):
                pick = verdict["symbol"]

        now = time.time()
        with self._lock:
            recent = {s["pick"] for s in self.scans
                      if s.get("pick") and now - s["ts"] < RECOMMEND_COOLDOWN_SECONDS}
            duplicate = pick in recent if pick else False
            self.scans.append({
                "ts": now, "prices": scanned_prices, "pick": pick,
                "top": [p["symbol"] for p in payload], "btc_regime": regime,
                "agent_source": source, "reason": reason, "duplicate": duplicate,
                "excluded": excluded, "r4": None, "r24": None,
            })
            cutoff = now - KEEP_DAYS * 86400
            self.scans = [s for s in self.scans if s["ts"] >= cutoff]
            self._save()

        log.info(f"🎯 AgentScanner: فحص {len(scanned_prices)} عملة | مرشحين: {len(payload)} | توصية: {pick} | مستبعدة: {excluded}")

        if pick and not duplicate and source == "gemini":
            self._notify_pick(pick, payload, reason)

    def _notify_pick(self, pick, payload, reason):
        d = next((p for p in payload if p["symbol"] == pick), {})
        pos_ar = {"below_val": "تحت منطقة القيمة", "val_to_poc": "قرب دعم حجمي، بين VAL وPOC",
                  "poc_to_vah": "داخل منطقة القيمة فوق POC", "above_vah": "فوق منطقة القيمة"}
        lines = [
            f"🎯 <b>توصية الوكيل: {pick.replace('USDT', '')}</b>  (توصية فقط — ما تم أي شراء)",
            f"💬 {reason}",
            f"💰 السعر: {d.get('price')} | النقاط: {d.get('score')}/11",
            f"📊 ADX {d.get('adx')} | MFI {d.get('mfi')} | RSI {d.get('rsi')} | Supertrend: {'صاعد' if d.get('supertrend_direction') == 'up' else d.get('supertrend_direction')}",
            f"📐 الموقع: {pos_ar.get(d.get('vp_position'), d.get('vp_position'))}",
            f"🛡️ أقرب دعم حجمي: {d.get('vp_stop_hint')} | 🎯 أقرب هدف حجمي: {d.get('vp_target_hint')}",
            f"🌡️ حالة BTC: {d.get('btc_regime')}",
        ]
        try:
            self.notify("\n".join(lines))
        except Exception as e:
            log.error(f"❌ AgentScanner: تعذر إرسال التوصية: {e}")

    # ──────────────────────────────────────────────
    # قياس النتائج بعد 4 و24 ساعة
    # ──────────────────────────────────────────────
    def evaluate_pending(self):
        now = time.time()
        with self._lock:
            pending = [s for s in self.scans
                       if any(s.get(k) is None and now - s["ts"] >= sec for k, sec in HORIZONS)]
        if not pending:
            return
        try:
            now_prices = {t["symbol"]: float(t["price"]) for t in self.client.get_all_tickers()}
        except Exception as e:
            log.error(f"❌ AgentScanner: تعذر جلب الأسعار للقياس: {e}")
            return

        def ret(sym, base):
            p0, p1 = base.get(sym), now_prices.get(sym)
            return (p1 - p0) / p0 * 100 if p0 and p1 else None

        with self._lock:
            for s in pending:
                for key, sec in HORIZONS:
                    if s.get(key) is not None or now - s["ts"] < sec:
                        continue
                    base = s["prices"]
                    market = [r for r in (ret(x, base) for x in base) if r is not None]
                    top = [r for r in (ret(x, base) for x in s["top"]) if r is not None]
                    s[key] = {
                        "market": round(sum(market) / len(market), 3) if market else None,
                        "top_avg": round(sum(top) / len(top), 3) if top else None,
                        "pick": round(ret(s["pick"], base), 3) if s["pick"] and ret(s["pick"], base) is not None else None,
                    }
                if s.get("r24") is not None:
                    s["prices"] = {}   # توفير مساحة بعد اكتمال القياس
            self._save()

    # ──────────────────────────────────────────────
    # تشخيص: ليش ما في توصيات؟
    # ──────────────────────────────────────────────
    _EXCL_AR = {
        "low_liquidity": "سيولة ضعيفة", "supertrend_down": "Supertrend هابط",
        "extended_above_vah": "ممتدة فوق منطقة القيمة", "weak_adx": "ADX ضعيف (<20)",
        "overbought_mfi": "MFI فوق 80", "weak_volume": "فوليوم ضعيف", "no_price": "بدون سعر",
    }

    def _diagnosis(self, scans):
        if not scans:
            return "\n🔎 لسا ما صار أي مسح"
        no_cand = sum(1 for s in scans if not s.get("top"))
        failed = sum(1 for s in scans if s.get("top") and s.get("agent_source") in ("error", "fallback"))
        rejected = sum(1 for s in scans if s.get("top") and s.get("agent_source") == "gemini" and not s.get("pick"))
        picked = sum(1 for s in scans if s.get("pick"))
        out = [
            "\n🔎 <b>تشخيص المسوحات</b>",
            f"• بدون مرشحين (القواعد استبعدت الكل): {no_cand}",
            f"• الوكيل رفض كل المرشحين: {rejected}",
            f"• تعذر التواصل مع Gemini أو معطّل: {failed}",
            f"• وكيل اختار عملة: {picked}",
        ]
        last = scans[-1]
        ex = last.get("excluded") or {}
        if ex:
            parts = [f"{self._EXCL_AR.get(k, k)}: {v}" for k, v in sorted(ex.items(), key=lambda x: -x[1])]
            out.append("\n<b>آخر مسح — سبب الاستبعاد:</b>\n" + " | ".join(parts))
        out.append(f"مرشحين وصلوا للوكيل بآخر مسح: {len(last.get('top') or [])}")
        if last.get("reason"):
            out.append(f"💬 آخر رد: {last['reason'][:300]}")
        return "\n".join(out)

    # ──────────────────────────────────────────────
    # التقرير
    # ──────────────────────────────────────────────
    def report(self):
        with self._lock:
            scans = list(self.scans)
        total_scans = len(scans)
        picks = [s for s in scans if s.get("pick") and s.get("agent_source") == "gemini" and not s.get("duplicate")]
        lines = [f"📈 <b>تقرير ماسح الوكيل</b>\nمسوحات: {total_scans} | توصيات: {len(picks)}"]
        lines.append(self._diagnosis(scans))
        for key, label in (("r4", "بعد 4 ساعات"), ("r24", "بعد 24 ساعة")):
            done = [s for s in picks if s.get(key) and s[key].get("pick") is not None and s[key].get("market") is not None]
            if not done:
                lines.append(f"\n⏳ {label}: لسا ما في توصيات مقاسة")
                continue
            n = len(done)
            avg_pick = sum(s[key]["pick"] for s in done) / n
            avg_mkt = sum(s[key]["market"] for s in done) / n
            beat = sum(1 for s in done if s[key]["pick"] > s[key]["market"])
            pos = sum(1 for s in done if s[key]["pick"] > 0)
            tops = [s[key]["top_avg"] for s in done if s[key].get("top_avg") is not None]
            top_txt = f" | متوسط أفضل 5 مرشحين: {sum(tops)/len(tops):+.2f}%" if tops else ""
            lines.append(
                f"\n<b>{label}</b> ({n} توصية)\n"
                f"• متوسط التوصيات: {avg_pick:+.2f}% | متوسط السوق: {avg_mkt:+.2f}%{top_txt}\n"
                f"• تفوّقت على السوق: {beat}/{n} ({beat/n*100:.0f}%) | رابحة: {pos}/{n} ({pos/n*100:.0f}%)"
            )
        if len(picks) < 20:
            lines.append("\n⚠️ العينة صغيرة (أقل من 20 توصية) — الأرقام غير حاسمة بعد. استنى قبل أي قرار.")
        return "\n".join(lines)

    # ──────────────────────────────────────────────
    # الحلقة الرئيسية
    # ──────────────────────────────────────────────
    def run(self):
        time.sleep(FIRST_SCAN_DELAY)
        while True:
            try:
                if self.enabled_fn():
                    now = time.time()
                    if now - self.last_scan >= SCAN_EVERY_SECONDS:
                        self.last_scan = now
                        self.scan_once()
                    self.evaluate_pending()
            except Exception as e:
                log.error(f"❌ AgentScanner: خطأ بالحلقة: {e}")
            time.sleep(60)
