"""
👀 Watch Orders — أوامر المراقبة من المحادثة ("راقب AVAX وSOL وإذا الدخول ممتاز اشتري")
=========================================================================================
- الأمر بينفعّل بس بعد تأكيد صريح من المستخدم (المحادثة تتولى التأكيد).
- المراقبة بالكود (مو Gemini): كل دورة بنفحص كل أمر نشط بنفس شروط دخول "ابحث عن صفقة"
  (فلتر MFI/SMA50/الفوليوم + موافقة بوابة المخاطر) عبر check_fn اللي بتنحقن من البوت.
- لو تحققت الشروط: open_fn بينفّذ الشراء بمسار البوت الأساسي (ستوب ATR + Trailing).
- كل أمر بيشتري مرة وحدة وبينتهي. وبينتهي لحاله بعد ttl (افتراضي 24 ساعة) بدون شراء.
- لو الشراء ممنوع مؤقتاً (التداول متوقف، رصيد، حد أقصى للصفقات، حظر بينانس) بينتظر
  وبيبعتلك تنبيه مرة كل ساعة بحد أقصى، بدل ما يتجاوز أو يلغي.

مستقل عن البوت الرئيسي: كل الاعتماديات بتنحقن بالـ constructor.
"""

import json
import logging
import os
import threading
import time

log = logging.getLogger(__name__)

DEFAULT_TTL_SECONDS = 24 * 3600
CHECK_INTERVAL_SECONDS = 120
BLOCK_NOTE_EVERY_SECONDS = 3600
MAX_ACTIVE_ORDERS = 5
KEEP_FINISHED_SECONDS = 7 * 24 * 3600


class WatchOrders:
    def __init__(self, path, check_fn, open_fn, notify_fn, ttl_seconds=DEFAULT_TTL_SECONDS):
        """
        check_fn(symbol) -> (entry_ok: bool, reason_ar: str, ind: dict|None)
        open_fn(symbol, ind, reason_ar) -> (status, message_ar)
            status: "bought" = تم الشراء | "wait" = ممنوع مؤقتاً (ننتظر) | "skip" = ما بدنا نشتري (ننهي الأمر)
        notify_fn(text_html)
        """
        self.path = path
        self.check_fn = check_fn
        self.open_fn = open_fn
        self.notify = notify_fn
        self.ttl = ttl_seconds
        self._lock = threading.Lock()
        self.orders = self._load()

    # ── تخزين ──
    def _load(self):
        try:
            if os.path.exists(self.path):
                with open(self.path, "r", encoding="utf-8") as f:
                    return json.load(f).get("orders", [])
        except Exception as e:
            log.error(f"❌ WatchOrders: تعذر قراءة السجل: {e}")
        return []

    def _save(self):
        try:
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"orders": self.orders}, f, ensure_ascii=False)
            os.replace(tmp, self.path)
        except Exception as e:
            log.error(f"❌ WatchOrders: تعذر حفظ السجل: {e}")

    # ── واجهة ──
    def active(self):
        with self._lock:
            return [dict(o) for o in self.orders if o["status"] == "active"]

    def add(self, symbols):
        """يرجع (المضافة, المتجاهلة[(رمز, سبب)])"""
        now = time.time()
        added, ignored = [], []
        with self._lock:
            active_syms = {o["symbol"] for o in self.orders if o["status"] == "active"}
            n_active = len(active_syms)
            for sym in symbols:
                if sym in active_syms:
                    ignored.append((sym, "عليها مراقبة نشطة أصلاً"))
                    continue
                if n_active >= MAX_ACTIVE_ORDERS:
                    ignored.append((sym, f"وصلنا للحد الأقصى ({MAX_ACTIVE_ORDERS} مراقبات نشطة)"))
                    continue
                self.orders.append({
                    "id": f"{int(now)}-{sym}", "symbol": sym, "created": now,
                    "expires": now + self.ttl, "status": "active",
                    "last_block_note": 0, "last_reason": "",
                })
                active_syms.add(sym)
                n_active += 1
                added.append(sym)
            self._save()
        return added, ignored

    def cancel(self, symbol):
        with self._lock:
            hit = False
            for o in self.orders:
                if o["symbol"] == symbol and o["status"] == "active":
                    o["status"] = "cancelled"
                    o["finished"] = time.time()
                    hit = True
            if hit:
                self._save()
            return hit

    def _finish(self, order, status, note=""):
        with self._lock:
            order["status"] = status
            order["finished"] = time.time()
            order["last_reason"] = note or order.get("last_reason", "")
            self._save()

    # ── دورة فحص ──
    def check_once(self):
        now = time.time()
        for o in self.active():
            sym = o["symbol"]
            coin = sym.replace("USDT", "")
            live = self._get(o["id"])
            if live is None or live["status"] != "active":
                continue

            if now >= live["expires"]:
                self._finish(live, "expired", "انتهت المدة بدون دخول")
                self._safe_notify(f"⌛ انتهت مراقبة <b>{coin}</b> بدون دخول مناسب (الدخول ما تحقق خلال المدة).")
                continue

            try:
                ok, reason, ind = self.check_fn(sym)
            except Exception as e:
                log.error(f"❌ WatchOrders check {sym}: {e}")
                continue
            with self._lock:
                live["last_reason"] = reason
            if not ok:
                continue

            try:
                status, msg = self.open_fn(sym, ind, reason)
            except Exception as e:
                log.error(f"❌ WatchOrders open {sym}: {e}")
                continue

            if status == "bought":
                self._finish(live, "done", "تم الشراء")
            elif status == "skip":
                self._finish(live, "skipped", msg)
                self._safe_notify(f"ℹ️ أنهيت مراقبة <b>{coin}</b>: {msg}")
            else:  # wait
                if now - live.get("last_block_note", 0) >= BLOCK_NOTE_EVERY_SECONDS:
                    with self._lock:
                        live["last_block_note"] = now
                        self._save()
                    self._safe_notify(f"⏳ <b>{coin}</b> وصلت لشروط الدخول بس الشراء متأجل: {msg}. المراقبة مستمرة.")

    def _get(self, oid):
        with self._lock:
            for o in self.orders:
                if o["id"] == oid:
                    return o
        return None

    def _safe_notify(self, text):
        try:
            self.notify(text)
        except Exception as e:
            log.error(f"❌ WatchOrders notify: {e}")

    def _prune(self):
        cutoff = time.time() - KEEP_FINISHED_SECONDS
        with self._lock:
            before = len(self.orders)
            self.orders = [o for o in self.orders
                           if o["status"] == "active" or o.get("finished", o["created"]) >= cutoff]
            if len(self.orders) != before:
                self._save()

    def run(self):
        time.sleep(30)
        while True:
            try:
                self.check_once()
                self._prune()
            except Exception as e:
                log.error(f"❌ WatchOrders: خطأ بالحلقة: {e}")
            time.sleep(CHECK_INTERVAL_SECONDS)
