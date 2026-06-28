import React, { useEffect, useState, useCallback } from "react";
import {
  View,
  Text,
  StyleSheet,
  ScrollView,
  TouchableOpacity,
  RefreshControl,
  ActivityIndicator,
  Alert,
} from "react-native";
import { ScreenContainer } from "@/components/screen-container";
import { getStatus, getOpenTrades, controlBot, BotStatus, OpenTrade } from "@/lib/bot-api";
import { useAuth } from "@/lib/auth-context";

const REFRESH_INTERVAL = 10000; // 10 ثوانٍ

export default function DashboardScreen() {
  const { logout } = useAuth();
  const [status, setStatus] = useState<BotStatus | null>(null);
  const [trades, setTrades] = useState<OpenTrade[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [toggling, setToggling] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchData = useCallback(async () => {
    try {
      const [s, t] = await Promise.all([getStatus(), getOpenTrades()]);
      setStatus(s);
      setTrades(t);
      setError(null);
    } catch (e: any) {
      if (e.message?.includes("unauthorized") || e.message?.includes("401")) {
        logout();
      } else {
        setError(e.message || "تعذر الاتصال بالسيرفر");
      }
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [logout]);

  useEffect(() => {
    fetchData();
    const interval = setInterval(fetchData, REFRESH_INTERVAL);
    return () => clearInterval(interval);
  }, [fetchData]);

  const onRefresh = useCallback(() => {
    setRefreshing(true);
    fetchData();
  }, [fetchData]);

  const handleToggleBot = async () => {
    if (!status) return;
    const action = status.trading_enabled ? "stop" : "start";
    const label = action === "stop" ? "إيقاف" : "تشغيل";
    Alert.alert(
      `${label} البوت`,
      `هل تريد ${label} التداول؟`,
      [
        { text: "إلغاء", style: "cancel" },
        {
          text: label,
          style: action === "stop" ? "destructive" : "default",
          onPress: async () => {
            setToggling(true);
            const result = await controlBot(action);
            if (result.ok) {
              setStatus((prev) => prev ? { ...prev, trading_enabled: !prev.trading_enabled } : prev);
            } else {
              Alert.alert("خطأ", result.error || "فشل التحكم بالبوت");
            }
            setToggling(false);
          },
        },
      ]
    );
  };

  if (loading) {
    return (
      <ScreenContainer>
        <View style={styles.center}>
          <ActivityIndicator size="large" color="#F0B90B" />
          <Text style={styles.loadingText}>جاري الاتصال بالبوت...</Text>
        </View>
      </ScreenContainer>
    );
  }

  if (error) {
    return (
      <ScreenContainer>
        <View style={styles.center}>
          <Text style={styles.errorIcon}>⚠️</Text>
          <Text style={styles.errorText}>{error}</Text>
          <TouchableOpacity style={styles.retryBtn} onPress={fetchData}>
            <Text style={styles.retryText}>إعادة المحاولة</Text>
          </TouchableOpacity>
        </View>
      </ScreenContainer>
    );
  }

  const pnlColor = (status?.total_pnl ?? 0) >= 0 ? "#00C087" : "#FF4D4F";
  const botActive = status?.trading_enabled ?? false;

  return (
    <ScreenContainer>
      <ScrollView
        style={styles.scroll}
        contentContainerStyle={styles.content}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor="#F0B90B" />}
        showsVerticalScrollIndicator={false}
      >
        {/* Header */}
        <View style={styles.header}>
          <View>
            <Text style={styles.headerTitle}>🤖 Crypto Bot</Text>
            <Text style={styles.headerSub}>لوحة التحكم</Text>
          </View>
          <View style={[styles.statusDot, { backgroundColor: botActive ? "#00C087" : "#FF4D4F" }]} />
        </View>

        {/* Circuit Breaker Banner */}
        {status?.circuit_breaker?.active && (
          <View style={styles.circuitBanner}>
            <Text style={styles.circuitIcon}>🛑</Text>
            <View style={{ flex: 1 }}>
              <Text style={styles.circuitTitle}>توقف تلقائي نشط</Text>
              <Text style={styles.circuitSub}>
                يستأنف خلال {status.circuit_breaker.resume_in_minutes} دقيقة
              </Text>
            </View>
          </View>
        )}

        {/* Bot Status Card */}
        <View style={styles.card}>
          <View style={styles.cardRow}>
            <View>
              <Text style={styles.cardLabel}>حالة البوت</Text>
              <Text style={[styles.statusText, { color: botActive ? "#00C087" : "#FF4D4F" }]}>
                {botActive ? "▶ شغال" : "⏸ متوقف"}
              </Text>
            </View>
            <TouchableOpacity
              style={[styles.toggleBtn, { backgroundColor: botActive ? "#FF4D4F22" : "#00C08722" }]}
              onPress={handleToggleBot}
              disabled={toggling}
            >
              {toggling ? (
                <ActivityIndicator size="small" color={botActive ? "#FF4D4F" : "#00C087"} />
              ) : (
                <Text style={[styles.toggleBtnText, { color: botActive ? "#FF4D4F" : "#00C087" }]}>
                  {botActive ? "إيقاف" : "تشغيل"}
                </Text>
              )}
            </TouchableOpacity>
          </View>
        </View>

        {/* Balance Card */}
        <View style={styles.card}>
          <Text style={styles.cardLabel}>رصيد USDT المتاح</Text>
          <Text style={styles.balanceText}>${status?.balance_usdt?.toFixed(2) ?? "0.00"}</Text>
        </View>

        {/* Stats Row */}
        <View style={styles.statsRow}>
          <View style={[styles.statCard, { flex: 1 }]}>
            <Text style={styles.statLabel}>إجمالي PnL</Text>
            <Text style={[styles.statValue, { color: pnlColor }]}>
              {(status?.total_pnl ?? 0) >= 0 ? "+" : ""}{status?.total_pnl?.toFixed(4) ?? "0"}$
            </Text>
          </View>
          <View style={[styles.statCard, { flex: 1 }]}>
            <Text style={styles.statLabel}>نسبة الفوز</Text>
            <Text style={[styles.statValue, { color: "#F0B90B" }]}>
              {status?.win_rate?.toFixed(1) ?? "0"}%
            </Text>
          </View>
          <View style={[styles.statCard, { flex: 1 }]}>
            <Text style={styles.statLabel}>الصفقات</Text>
            <Text style={[styles.statValue, { color: "#E6EDF3" }]}>
              {status?.open_trades_count ?? 0}/{status?.max_trades ?? 0}
            </Text>
          </View>
        </View>

        {/* Watch List Info */}
        <View style={styles.infoRow}>
          <Text style={styles.infoText}>
            👁 يراقب {status?.watch_count ?? 0} عملة من أصل {status?.symbols_count ?? 0}
          </Text>
        </View>

        {/* Open Trades */}
        <Text style={styles.sectionTitle}>الصفقات المفتوحة</Text>
        {trades.length === 0 ? (
          <View style={styles.emptyCard}>
            <Text style={styles.emptyText}>لا توجد صفقات مفتوحة حالياً</Text>
          </View>
        ) : (
          trades.map((trade) => (
            <TradeCard key={trade.symbol} trade={trade} />
          ))
        )}
      </ScrollView>
    </ScreenContainer>
  );
}

function TradeCard({ trade }: { trade: OpenTrade }) {
  const pnlColor = trade.pnl_pct >= 0 ? "#00C087" : "#FF4D4F";
  const coin = trade.symbol.replace("USDT", "");

  return (
    <View style={styles.tradeCard}>
      <View style={styles.tradeHeader}>
        <Text style={styles.tradeCoin}>{coin}/USDT</Text>
        <View style={styles.tradeRight}>
          <Text style={[styles.tradePnl, { color: pnlColor }]}>
            {trade.pnl_pct >= 0 ? "+" : ""}{trade.pnl_pct.toFixed(2)}%
          </Text>
          {trade.trailing_active && (
            <View style={styles.trailingBadge}>
              <Text style={styles.trailingText}>Trailing</Text>
            </View>
          )}
        </View>
      </View>
      <View style={styles.tradePrices}>
        <View style={styles.priceItem}>
          <Text style={styles.priceLabel}>دخول</Text>
          <Text style={styles.priceValue}>${trade.entry_price.toFixed(4)}</Text>
        </View>
        <View style={styles.priceItem}>
          <Text style={styles.priceLabel}>الحالي</Text>
          <Text style={[styles.priceValue, { color: pnlColor }]}>${trade.current_price.toFixed(4)}</Text>
        </View>
        {trade.stop_loss && (
          <View style={styles.priceItem}>
            <Text style={styles.priceLabel}>Stop Loss</Text>
            <Text style={[styles.priceValue, { color: "#FF4D4F" }]}>${trade.stop_loss.toFixed(4)}</Text>
          </View>
        )}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  scroll: { flex: 1 },
  content: { padding: 16, paddingBottom: 32, gap: 12 },
  center: { flex: 1, alignItems: "center", justifyContent: "center", gap: 12 },
  loadingText: { color: "#8B949E", fontSize: 14 },
  errorIcon: { fontSize: 40 },
  errorText: { color: "#FF4D4F", fontSize: 14, textAlign: "center" },
  retryBtn: { backgroundColor: "#F0B90B", paddingHorizontal: 24, paddingVertical: 10, borderRadius: 8 },
  retryText: { color: "#0D1117", fontWeight: "bold" },

  header: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    marginBottom: 4,
  },
  headerTitle: { color: "#E6EDF3", fontSize: 22, fontWeight: "bold" },
  headerSub: { color: "#8B949E", fontSize: 12 },
  statusDot: { width: 12, height: 12, borderRadius: 6 },

  circuitBanner: {
    backgroundColor: "#F59E0B22",
    borderWidth: 1,
    borderColor: "#F59E0B",
    borderRadius: 12,
    padding: 12,
    flexDirection: "row",
    alignItems: "center",
    gap: 10,
  },
  circuitIcon: { fontSize: 24 },
  circuitTitle: { color: "#F59E0B", fontWeight: "bold", fontSize: 14 },
  circuitSub: { color: "#8B949E", fontSize: 12 },

  card: {
    backgroundColor: "#161B22",
    borderRadius: 14,
    padding: 16,
    borderWidth: 1,
    borderColor: "#30363D",
  },
  cardRow: { flexDirection: "row", justifyContent: "space-between", alignItems: "center" },
  cardLabel: { color: "#8B949E", fontSize: 12, marginBottom: 4 },
  statusText: { fontSize: 18, fontWeight: "bold" },
  toggleBtn: {
    paddingHorizontal: 20,
    paddingVertical: 8,
    borderRadius: 8,
    minWidth: 80,
    alignItems: "center",
  },
  toggleBtnText: { fontWeight: "bold", fontSize: 14 },
  balanceText: { color: "#E6EDF3", fontSize: 28, fontWeight: "bold" },

  statsRow: { flexDirection: "row", gap: 8 },
  statCard: {
    backgroundColor: "#161B22",
    borderRadius: 12,
    padding: 12,
    borderWidth: 1,
    borderColor: "#30363D",
    alignItems: "center",
  },
  statLabel: { color: "#8B949E", fontSize: 11, marginBottom: 4 },
  statValue: { fontSize: 15, fontWeight: "bold" },

  infoRow: {
    backgroundColor: "#161B22",
    borderRadius: 10,
    padding: 10,
    borderWidth: 1,
    borderColor: "#30363D",
  },
  infoText: { color: "#8B949E", fontSize: 13, textAlign: "center" },

  sectionTitle: { color: "#E6EDF3", fontSize: 16, fontWeight: "bold", marginTop: 4 },

  emptyCard: {
    backgroundColor: "#161B22",
    borderRadius: 12,
    padding: 24,
    alignItems: "center",
    borderWidth: 1,
    borderColor: "#30363D",
  },
  emptyText: { color: "#8B949E", fontSize: 14 },

  tradeCard: {
    backgroundColor: "#161B22",
    borderRadius: 12,
    padding: 14,
    borderWidth: 1,
    borderColor: "#30363D",
    gap: 10,
  },
  tradeHeader: { flexDirection: "row", justifyContent: "space-between", alignItems: "center" },
  tradeCoin: { color: "#E6EDF3", fontSize: 16, fontWeight: "bold" },
  tradeRight: { flexDirection: "row", alignItems: "center", gap: 8 },
  tradePnl: { fontSize: 16, fontWeight: "bold" },
  trailingBadge: {
    backgroundColor: "#F0B90B22",
    borderWidth: 1,
    borderColor: "#F0B90B",
    borderRadius: 6,
    paddingHorizontal: 6,
    paddingVertical: 2,
  },
  trailingText: { color: "#F0B90B", fontSize: 10, fontWeight: "bold" },
  tradePrices: { flexDirection: "row", gap: 16 },
  priceItem: { gap: 2 },
  priceLabel: { color: "#8B949E", fontSize: 11 },
  priceValue: { color: "#E6EDF3", fontSize: 13, fontWeight: "600" },
});
