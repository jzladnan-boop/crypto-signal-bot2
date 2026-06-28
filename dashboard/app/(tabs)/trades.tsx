import React, { useEffect, useState, useCallback } from "react";
import {
  View,
  Text,
  StyleSheet,
  FlatList,
  TouchableOpacity,
  RefreshControl,
  ActivityIndicator,
} from "react-native";
import { ScreenContainer } from "@/components/screen-container";
import { getOpenTrades, getHistory, OpenTrade, HistoryRecord } from "@/lib/bot-api";
import { useAuth } from "@/lib/auth-context";

type Tab = "open" | "history";
type Period = "today" | "week" | "all";

export default function TradesScreen() {
  const { logout } = useAuth();
  const [activeTab, setActiveTab] = useState<Tab>("open");
  const [period, setPeriod] = useState<Period>("all");
  const [openTrades, setOpenTrades] = useState<OpenTrade[]>([]);
  const [history, setHistory] = useState<HistoryRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);

  const fetchData = useCallback(async () => {
    try {
      if (activeTab === "open") {
        const data = await getOpenTrades();
        setOpenTrades(data);
      } else {
        const data = await getHistory(period);
        setHistory(data);
      }
    } catch (e: any) {
      if (e.message?.includes("unauthorized") || e.message?.includes("401")) logout();
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [activeTab, period, logout]);

  useEffect(() => {
    setLoading(true);
    fetchData();
  }, [activeTab, period]);

  const onRefresh = useCallback(() => {
    setRefreshing(true);
    fetchData();
  }, [fetchData]);

  // Stats for history
  const totalProfit = history.reduce((s, r) => s + r.profit, 0);
  const wins = history.filter((r) => r.profit > 0).length;
  const losses = history.filter((r) => r.profit <= 0).length;

  return (
    <ScreenContainer>
      <View style={styles.container}>
        {/* Header */}
        <View style={styles.header}>
          <Text style={styles.title}>الصفقات</Text>
        </View>

        {/* Tab Switcher */}
        <View style={styles.tabBar}>
          <TouchableOpacity
            style={[styles.tab, activeTab === "open" && styles.tabActive]}
            onPress={() => setActiveTab("open")}
          >
            <Text style={[styles.tabText, activeTab === "open" && styles.tabTextActive]}>
              المفتوحة {openTrades.length > 0 ? `(${openTrades.length})` : ""}
            </Text>
          </TouchableOpacity>
          <TouchableOpacity
            style={[styles.tab, activeTab === "history" && styles.tabActive]}
            onPress={() => setActiveTab("history")}
          >
            <Text style={[styles.tabText, activeTab === "history" && styles.tabTextActive]}>
              السجل
            </Text>
          </TouchableOpacity>
        </View>

        {/* Period Filter (History only) */}
        {activeTab === "history" && (
          <View style={styles.periodRow}>
            {(["today", "week", "all"] as Period[]).map((p) => (
              <TouchableOpacity
                key={p}
                style={[styles.periodBtn, period === p && styles.periodBtnActive]}
                onPress={() => setPeriod(p)}
              >
                <Text style={[styles.periodText, period === p && styles.periodTextActive]}>
                  {p === "today" ? "اليوم" : p === "week" ? "الأسبوع" : "الكل"}
                </Text>
              </TouchableOpacity>
            ))}
          </View>
        )}

        {/* History Stats */}
        {activeTab === "history" && history.length > 0 && (
          <View style={styles.statsBar}>
            <View style={styles.statItem}>
              <Text style={styles.statLabel}>إجمالي</Text>
              <Text style={[styles.statValue, { color: totalProfit >= 0 ? "#00C087" : "#FF4D4F" }]}>
                {totalProfit >= 0 ? "+" : ""}{totalProfit.toFixed(4)}$
              </Text>
            </View>
            <View style={styles.statItem}>
              <Text style={styles.statLabel}>رابحة</Text>
              <Text style={[styles.statValue, { color: "#00C087" }]}>{wins}</Text>
            </View>
            <View style={styles.statItem}>
              <Text style={styles.statLabel}>خاسرة</Text>
              <Text style={[styles.statValue, { color: "#FF4D4F" }]}>{losses}</Text>
            </View>
            <View style={styles.statItem}>
              <Text style={styles.statLabel}>نسبة الفوز</Text>
              <Text style={[styles.statValue, { color: "#F0B90B" }]}>
                {history.length > 0 ? ((wins / history.length) * 100).toFixed(0) : 0}%
              </Text>
            </View>
          </View>
        )}

        {/* Content */}
        {loading ? (
          <View style={styles.center}>
            <ActivityIndicator size="large" color="#F0B90B" />
          </View>
        ) : activeTab === "open" ? (
          <FlatList
            data={openTrades}
            keyExtractor={(item) => item.symbol}
            renderItem={({ item }) => <OpenTradeItem trade={item} />}
            ListEmptyComponent={<EmptyState text="لا توجد صفقات مفتوحة" />}
            refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor="#F0B90B" />}
            contentContainerStyle={styles.listContent}
            showsVerticalScrollIndicator={false}
          />
        ) : (
          <FlatList
            data={history}
            keyExtractor={(item, idx) => `${item.symbol}-${item.time}-${idx}`}
            renderItem={({ item }) => <HistoryItem record={item} />}
            ListEmptyComponent={<EmptyState text="لا توجد صفقات مغلقة في هذه الفترة" />}
            refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor="#F0B90B" />}
            contentContainerStyle={styles.listContent}
            showsVerticalScrollIndicator={false}
          />
        )}
      </View>
    </ScreenContainer>
  );
}

function OpenTradeItem({ trade }: { trade: OpenTrade }) {
  const coin = trade.symbol.replace("USDT", "");
  const pnlColor = trade.pnl_pct >= 0 ? "#00C087" : "#FF4D4F";

  return (
    <View style={styles.tradeCard}>
      <View style={styles.tradeTop}>
        <Text style={styles.tradeCoin}>{coin}/USDT</Text>
        <View style={styles.tradeRight}>
          <Text style={[styles.tradePnl, { color: pnlColor }]}>
            {trade.pnl_pct >= 0 ? "+" : ""}{trade.pnl_pct.toFixed(2)}%
          </Text>
          {trade.trailing_active && (
            <View style={styles.badge}>
              <Text style={styles.badgeText}>🎯 Trailing</Text>
            </View>
          )}
        </View>
      </View>
      <View style={styles.priceRow}>
        <PriceBox label="دخول" value={`$${trade.entry_price.toFixed(4)}`} />
        <PriceBox label="الحالي" value={`$${trade.current_price.toFixed(4)}`} color={pnlColor} />
        {trade.stop_loss && (
          <PriceBox label="Stop Loss" value={`$${trade.stop_loss.toFixed(4)}`} color="#FF4D4F" />
        )}
      </View>
    </View>
  );
}

function HistoryItem({ record }: { record: HistoryRecord }) {
  const coin = record.symbol.replace("USDT", "");
  const isWin = record.profit > 0;
  const profitColor = isWin ? "#00C087" : "#FF4D4F";
  const reasonLabels: Record<string, string> = {
    trailing_stop: "🎯 Trailing Stop",
    stop_loss: "🛑 Stop Loss",
    manual: "✋ يدوي",
  };

  return (
    <View style={styles.historyCard}>
      <View style={styles.historyTop}>
        <View>
          <Text style={styles.historyCoin}>{coin}/USDT</Text>
          <Text style={styles.historyReason}>{reasonLabels[record.reason] || record.reason}</Text>
        </View>
        <View style={styles.historyRight}>
          <Text style={[styles.historyProfit, { color: profitColor }]}>
            {isWin ? "+" : ""}{record.profit.toFixed(4)}$
          </Text>
          <Text style={styles.historyTime}>{record.time.substring(0, 16)}</Text>
        </View>
      </View>
      <View style={styles.priceRow}>
        <PriceBox label="دخول" value={`$${record.entry_price.toFixed(4)}`} />
        <PriceBox label="خروج" value={`$${record.exit_price.toFixed(4)}`} color={profitColor} />
        <PriceBox label="الكمية" value={record.qty.toFixed(4)} />
      </View>
    </View>
  );
}

function PriceBox({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <View style={styles.priceBox}>
      <Text style={styles.priceLabel}>{label}</Text>
      <Text style={[styles.priceValue, color ? { color } : {}]}>{value}</Text>
    </View>
  );
}

function EmptyState({ text }: { text: string }) {
  return (
    <View style={styles.emptyState}>
      <Text style={styles.emptyIcon}>📭</Text>
      <Text style={styles.emptyText}>{text}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: "#0D1117" },
  header: { paddingHorizontal: 16, paddingTop: 16, paddingBottom: 8 },
  title: { color: "#E6EDF3", fontSize: 22, fontWeight: "bold" },
  center: { flex: 1, alignItems: "center", justifyContent: "center" },
  listContent: { padding: 16, paddingBottom: 32, gap: 10 },

  tabBar: {
    flexDirection: "row",
    marginHorizontal: 16,
    backgroundColor: "#161B22",
    borderRadius: 10,
    padding: 4,
    marginBottom: 12,
    borderWidth: 1,
    borderColor: "#30363D",
  },
  tab: { flex: 1, paddingVertical: 8, alignItems: "center", borderRadius: 8 },
  tabActive: { backgroundColor: "#F0B90B" },
  tabText: { color: "#8B949E", fontWeight: "600", fontSize: 14 },
  tabTextActive: { color: "#0D1117" },

  periodRow: {
    flexDirection: "row",
    marginHorizontal: 16,
    gap: 8,
    marginBottom: 10,
  },
  periodBtn: {
    flex: 1,
    paddingVertical: 6,
    alignItems: "center",
    borderRadius: 8,
    borderWidth: 1,
    borderColor: "#30363D",
    backgroundColor: "#161B22",
  },
  periodBtnActive: { borderColor: "#F0B90B", backgroundColor: "#F0B90B22" },
  periodText: { color: "#8B949E", fontSize: 13 },
  periodTextActive: { color: "#F0B90B", fontWeight: "bold" },

  statsBar: {
    flexDirection: "row",
    marginHorizontal: 16,
    backgroundColor: "#161B22",
    borderRadius: 10,
    padding: 12,
    marginBottom: 10,
    borderWidth: 1,
    borderColor: "#30363D",
  },
  statItem: { flex: 1, alignItems: "center" },
  statLabel: { color: "#8B949E", fontSize: 11, marginBottom: 2 },
  statValue: { fontSize: 13, fontWeight: "bold" },

  tradeCard: {
    backgroundColor: "#161B22",
    borderRadius: 12,
    padding: 14,
    borderWidth: 1,
    borderColor: "#30363D",
    gap: 10,
  },
  tradeTop: { flexDirection: "row", justifyContent: "space-between", alignItems: "center" },
  tradeCoin: { color: "#E6EDF3", fontSize: 16, fontWeight: "bold" },
  tradeRight: { flexDirection: "row", alignItems: "center", gap: 8 },
  tradePnl: { fontSize: 16, fontWeight: "bold" },
  badge: {
    backgroundColor: "#F0B90B22",
    borderWidth: 1,
    borderColor: "#F0B90B",
    borderRadius: 6,
    paddingHorizontal: 6,
    paddingVertical: 2,
  },
  badgeText: { color: "#F0B90B", fontSize: 10, fontWeight: "bold" },

  historyCard: {
    backgroundColor: "#161B22",
    borderRadius: 12,
    padding: 14,
    borderWidth: 1,
    borderColor: "#30363D",
    gap: 10,
  },
  historyTop: { flexDirection: "row", justifyContent: "space-between", alignItems: "flex-start" },
  historyCoin: { color: "#E6EDF3", fontSize: 15, fontWeight: "bold" },
  historyReason: { color: "#8B949E", fontSize: 12, marginTop: 2 },
  historyRight: { alignItems: "flex-end" },
  historyProfit: { fontSize: 16, fontWeight: "bold" },
  historyTime: { color: "#8B949E", fontSize: 11, marginTop: 2 },

  priceRow: { flexDirection: "row", gap: 12 },
  priceBox: { gap: 2 },
  priceLabel: { color: "#8B949E", fontSize: 11 },
  priceValue: { color: "#E6EDF3", fontSize: 13, fontWeight: "600" },

  emptyState: { alignItems: "center", paddingVertical: 60, gap: 12 },
  emptyIcon: { fontSize: 40 },
  emptyText: { color: "#8B949E", fontSize: 14 },
});
