/**
 * Bot API Client
 * يتصل بـ Flask API الخاص ببوت RSI Auto Trader
 */

import AsyncStorage from "@react-native-async-storage/async-storage";

const SERVER_URL_KEY = "bot_server_url";
const SESSION_COOKIE_KEY = "bot_session_cookie";

export async function getServerUrl(): Promise<string> {
  const url = await AsyncStorage.getItem(SERVER_URL_KEY);
  return url || "";
}

export async function setServerUrl(url: string): Promise<void> {
  await AsyncStorage.setItem(SERVER_URL_KEY, url.replace(/\/$/, ""));
}

export async function getSessionCookie(): Promise<string | null> {
  return AsyncStorage.getItem(SESSION_COOKIE_KEY);
}

export async function setSessionCookie(cookie: string): Promise<void> {
  await AsyncStorage.setItem(SESSION_COOKIE_KEY, cookie);
}

export async function clearSession(): Promise<void> {
  await AsyncStorage.removeItem(SESSION_COOKIE_KEY);
}

async function apiFetch(path: string, options: RequestInit = {}): Promise<Response> {
  const baseUrl = await getServerUrl();
  if (!baseUrl) throw new Error("لم يتم تحديد عنوان السيرفر");

  const cookie = await getSessionCookie();
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(options.headers as Record<string, string>),
  };
  if (cookie) headers["Cookie"] = cookie;

  const response = await fetch(`${baseUrl}${path}`, {
    ...options,
    headers,
    credentials: "include",
  });

  // حفظ الـ session cookie من الاستجابة
  const setCookie = response.headers.get("set-cookie");
  if (setCookie) {
    const sessionMatch = setCookie.match(/session=[^;]+/);
    if (sessionMatch) await setSessionCookie(sessionMatch[0]);
  }

  return response;
}

// ── Auth ──────────────────────────────────────────────

export async function login(username: string, password: string): Promise<{ ok: boolean; error?: string }> {
  try {
    const res = await apiFetch("/api/login", {
      method: "POST",
      body: JSON.stringify({ username, password }),
    });
    const data = await res.json();
    if (res.ok && data.ok) return { ok: true };
    return { ok: false, error: data.error || "خطأ في تسجيل الدخول" };
  } catch (e: any) {
    return { ok: false, error: e.message || "تعذر الاتصال بالسيرفر" };
  }
}

export async function logout(): Promise<void> {
  try {
    await apiFetch("/api/logout", { method: "POST" });
  } catch {}
  await clearSession();
}

export async function checkAuth(): Promise<boolean> {
  try {
    const res = await apiFetch("/api/me");
    const data = await res.json();
    return data.logged_in === true;
  } catch {
    return false;
  }
}

// ── Status ────────────────────────────────────────────

export interface BotStatus {
  trading_enabled: boolean;
  circuit_breaker: { active: boolean; resume_in_minutes: number };
  balance_usdt: number;
  open_trades_count: number;
  max_trades: number;
  watch_count: number;
  symbols_count: number;
  win_rate: number;
  total_pnl: number;
}

export async function getStatus(): Promise<BotStatus> {
  const res = await apiFetch("/api/status");
  if (!res.ok) throw new Error("فشل جلب الحالة");
  return res.json();
}

// ── Trades ────────────────────────────────────────────

export interface OpenTrade {
  symbol: string;
  entry_price: number;
  current_price: number;
  trailing_active: boolean;
  stop_loss: number | null;
  pnl_pct: number;
}

export async function getOpenTrades(): Promise<OpenTrade[]> {
  const res = await apiFetch("/api/trades");
  if (!res.ok) throw new Error("فشل جلب الصفقات");
  return res.json();
}

export interface HistoryRecord {
  symbol: string;
  entry_price: number;
  exit_price: number;
  qty: number;
  profit: number;
  reason: string;
  time: string;
}

export async function getHistory(period: "today" | "week" | "all" = "all"): Promise<HistoryRecord[]> {
  const res = await apiFetch(`/api/history?period=${period}`);
  if (!res.ok) throw new Error("فشل جلب السجل");
  return res.json();
}

// ── Settings ──────────────────────────────────────────

export interface BotSettings {
  trade_amount: number;
  max_trades: number;
  trail_pct: number;
  interval_minutes: number;
  rsi_low: number;
  rsi_high: number;
  rsi_enabled: boolean;
  stochastic_enabled: boolean;
  ma20_enabled: boolean;
  stop_loss_pct?: number;
  activate_trailing_pct?: number;
  trading_pairs?: string[];
  close_position?: string;
}

export async function getSettings(): Promise<BotSettings> {
  const res = await apiFetch("/api/settings");
  if (!res.ok) throw new Error("فشل جلب الإعدادات");
  return res.json();
}

export async function updateSettings(settings: Partial<BotSettings>): Promise<{ ok: boolean; error?: string }> {
  try {
    const res = await apiFetch("/api/settings", {
      method: "POST",
      body: JSON.stringify(settings),
    });
    const data = await res.json();
    if (res.ok && data.ok) return { ok: true };
    return { ok: false, error: data.error || "فشل تحديث الإعدادات" };
  } catch (e: any) {
    return { ok: false, error: e.message };
  }
}

// ── Control ───────────────────────────────────────────

export async function controlBot(action: "start" | "stop"): Promise<{ ok: boolean; error?: string }> {
  try {
    const res = await apiFetch("/api/control", {
      method: "POST",
      body: JSON.stringify({ action }),
    });
    const data = await res.json();
    if (res.ok && data.ok) return { ok: true };
    return { ok: false, error: data.error || "فشل التحكم بالبوت" };
  } catch (e: any) {
    return { ok: false, error: e.message };
  }
}
// ── Push Notifications ───────────────────────────

export async function registerPushToken(): Promise<{ ok: boolean; error?: string }> {
  try {
    const Notifications = await import("expo-notifications");
    const Device = await import("expo-device");

    if (!Device.isDevice) {
      return { ok: false, error: "يعمل فقط على جهاز حقيقي" };
    }

    const { status: existingStatus } = await Notifications.getPermissionsAsync();
    let finalStatus = existingStatus;
    if (existingStatus !== "granted") {
      const { status } = await Notifications.requestPermissionsAsync();
      finalStatus = status;
    }
    if (finalStatus !== "granted") {
      return { ok: false, error: "لم يتم منح إذن الإشعارات" };
    }

    const projectId = "67ee0e3a-26b8-4969-a2b0-d566f875e6a7";
    const tokenData = await Notifications.getExpoPushTokenAsync({ projectId });
    const token = tokenData.data;

    const result = await apiFetch("/api/register_push", {
      method: "POST",
      body: JSON.stringify({ token }),
    });
    const data = await result.json();
    if (result.ok && data.ok) return { ok: true };
    return { ok: false, error: data.error || "فشل تسجيل الإشعارات" };
  } catch (e: any) {
    return { ok: false, error: e.message };
  }
}