import React, { useEffect, useState, useCallback } from "react";
import {
  View,
  Text,
  StyleSheet,
  ScrollView,
  TouchableOpacity,
  TextInput,
  ActivityIndicator,
  Alert,
  Switch,
} from "react-native";
import { ScreenContainer } from "@/components/screen-container";
import {
  getSettings,
  updateSettings,
  BotSettings,
  getSymbols,
  addSymbol,
  removeSymbol,
  closeTrade,
} from "@/lib/bot-api";
import { useAuth } from "@/lib/auth-context";

const INTERVAL_OPTIONS = [15, 30, 60, 240];

export default function SettingsScreen() {
  const { logout } = useAuth();
  const [settings, setSettings] = useState<BotSettings | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [edited, setEdited] = useState<Partial<BotSettings>>({});

  // Symbols
  const [symbols, setSymbols] = useState<string[]>([]);
  const [symbolInput, setSymbolInput] = useState("");
  const [symbolLoading, setSymbolLoading] = useState(false);

  // Close Trade
  const [closeInput, setCloseInput] = useState("");
  const [closeLoading, setCloseLoading] = useState(false);

  const fetchSettings = useCallback(async () => {
    try {
      const data = await getSettings();
      setSettings(data);
      setEdited({});
    } catch (e: any) {
      if (e.message?.includes("unauthorized")) logout();
    } finally {
      setLoading(false);
    }
  }, [logout]);

  const fetchSymbols = useCallback(async () => {
    const list = await getSymbols();
    setSymbols(list);
  }, []);

  useEffect(() => {
    fetchSettings();
    fetchSymbols();
  }, [fetchSettings, fetchSymbols]);

  const handleSave = async () => {
    if (Object.keys(edited).length === 0) {
      Alert.alert("تنبيه", "لم تقم بتعديل أي إعداد");
      return;
    }
    setSaving(true);
    const result = await updateSettings(edited);
    if (result.ok) {
      Alert.alert("✅ تم الحفظ", "تم تحديث الإعدادات بنجاح");
      fetchSettings();
    } else {
      Alert.alert("خطأ", result.error || "فشل حفظ الإعدادات");
    }
    setSaving(false);
  };

  const handleAddSymbol = async () => {
    if (!symbolInput.trim()) return;
    setSymbolLoading(true);
    const result = await addSymbol(symbolInput.trim());
    if (result.ok) {
      Alert.alert("✅ تمت الإضافة", `تم إضافة ${symbolInput.toUpperCase()}`);
      setSymbolInput("");
      fetchSymbols();
    } else {
      Alert.alert("خطأ", result.error || "فشل إضافة العملة");
    }
    setSymbolLoading(false);
  };

  const handleRemoveSymbol = async (sym: string) => {
    Alert.alert("تأكيد", `هل تريد حذف ${sym}؟`, [
      { text: "إلغاء", style: "cancel" },
      {
        text: "حذف",
        style: "destructive",
        onPress: async () => {
          const result = await removeSymbol(sym);
          if (result.ok) {
            fetchSymbols();
          } else {
            Alert.alert("خطأ", result.error || "فشل حذف العملة");
          }
        },
      },
    ]);
  };

  const handleCloseTrade = async () => {
    if (!closeInput.trim()) return;
    Alert.alert("تأكيد إغلاق", `هل تريد إغلاق صفقة ${closeInput.toUpperCase()}؟`, [
      { text: "إلغاء", style: "cancel" },
      {
        text: "إغلاق",
        style: "destructive",
        onPress: async () => {
          setCloseLoading(true);
          const result = await closeTrade(closeInput.trim());
          if (result.ok) {
            Alert.alert("✅ تم الإغلاق", `تم إغلاق صفقة ${closeInput.toUpperCase()}`);
            setCloseInput("");
          } else {
            Alert.alert("خطأ", result.error || "فشل إغلاق الصفقة");
          }
          setCloseLoading(false);
        },
      },
    ]);
  };

  const current = { ...settings, ...edited } as BotSettings;

  const updateField = (key: keyof BotSettings, value: any) => {
    setEdited((prev) => ({ ...prev, [key]: value }));
  };

  if (loading) {
    return (
      <ScreenContainer>
        <View style={styles.center}>
          <ActivityIndicator size="large" color="#F0B90B" />
        </View>
      </ScreenContainer>
    );
  }

  return (
    <ScreenContainer>
      <ScrollView
        style={styles.scroll}
        contentContainerStyle={styles.content}
        showsVerticalScrollIndicator={false}
      >
        <Text style={styles.pageTitle}>الإعدادات</Text>

        {/* ── معاملات التداول ── */}
        <SectionHeader title="⚙️ معاملات التداول" />
        <View style={styles.card}>
          <NumberRow
            label="حجم الصفقة ($)"
            value={current.trade_amount}
            onChange={(v) => updateField("trade_amount", v)}
            hint="مبلغ كل صفقة بالدولار"
          />
          <Divider />
          <NumberRow
            label="أقصى عدد صفقات"
            value={current.max_trades}
            onChange={(v) => updateField("max_trades", v)}
            hint="الحد الأقصى للصفقات المفتوحة"
          />
          <Divider />
          <NumberRow
            label="Trailing Stop (%)"
            value={current.trail_pct}
            onChange={(v) => updateField("trail_pct", v)}
            hint="نسبة تنفس Trailing Stop"
            decimal
          />
          <Divider />
          <NumberRow
            label="Stop Loss (%)"
            value={current.stoploss_pct}
            onChange={(v) => updateField("stoploss_pct", v)}
            hint="حد الخسارة الثابت قبل تفعيل Trailing"
            decimal
          />
          <Divider />
          <NumberRow
            label="Activate Trailing (%)"
            value={current.activate_pct}
            onChange={(v) => updateField("activate_pct", v)}
            hint="نسبة الربح المطلوبة لتفعيل Trailing Stop"
            decimal
          />
        </View>

        {/* ── إغلاق صفقة ── */}
        <SectionHeader title="🔴 إغلاق صفقة" />
        <View style={styles.card}>
          <View style={styles.row}>
            <TextInput
              style={[styles.symbolInput, { flex: 1 }]}
              placeholder="اسم العملة (مثال: BTC)"
              placeholderTextColor="#8B949E"
              value={closeInput}
              onChangeText={setCloseInput}
              autoCapitalize="characters"
            />
            <TouchableOpacity
              style={[styles.actionBtn, { backgroundColor: "#FF4444" }]}
              onPress={handleCloseTrade}
              disabled={closeLoading}
            >
              {closeLoading ? (
                <ActivityIndicator color="#fff" size="small" />
              ) : (
                <Text style={styles.actionBtnText}>إغلاق</Text>
              )}
            </TouchableOpacity>
          </View>
        </View>

        {/* ── إعدادات RSI ── */}
        <SectionHeader title="📊 إعدادات RSI" />
        <View style={styles.card}>
          {/* RSI Enable/Disable + Range */}
          <View style={styles.row}>
            <View style={{ flex: 1 }}>
              <Text style={styles.rowLabel}>RSI العادي</Text>
              <Text style={styles.rowHint}>شراء عند ارتداد RSI فوق 30</Text>
            </View>
            <Switch
              value={current.rsi_enabled ?? true}
              onValueChange={(v) => updateField("rsi_enabled", v)}
              trackColor={{ false: "#30363D", true: "#F0B90B44" }}
              thumbColor={current.rsi_enabled ? "#F0B90B" : "#8B949E"}
            />
          </View>
          {/* RSI Low & High في سطر واحد */}
          <Divider />
          <View style={styles.row}>
            <View style={{ flex: 1 }}>
              <Text style={styles.rowLabel}>نطاق RSI</Text>
              <Text style={styles.rowHint}>Low / High للمراقبة</Text>
            </View>
            <View style={styles.dualInput}>
              <TextInput
                style={styles.dualField}
                value={edited.rsi_low?.toString() ?? current.rsi_low?.toString() ?? ""}
                onChangeText={(t) => {
                  const n = parseFloat(t);
                  if (!isNaN(n)) updateField("rsi_low", n);
                }}
                keyboardType="decimal-pad"
                placeholder="Low"
                placeholderTextColor="#8B949E"
              />
              <Text style={styles.dualSep}>/</Text>
              <TextInput
                style={styles.dualField}
                value={edited.rsi_high?.toString() ?? current.rsi_high?.toString() ?? ""}
                onChangeText={(t) => {
                  const n = parseFloat(t);
                  if (!isNaN(n)) updateField("rsi_high", n);
                }}
                keyboardType="decimal-pad"
                placeholder="High"
                placeholderTextColor="#8B949E"
              />
            </View>
          </View>

          {/* Stochastic RSI */}
          <Divider />
          <View style={styles.row}>
            <View style={{ flex: 1 }}>
              <Text style={styles.rowLabel}>Stochastic RSI</Text>
              <Text style={styles.rowHint}>شراء عند تقاطع واختراق مستوى 20</Text>
            </View>
            <Switch
              value={current.stoch_rsi_enabled ?? false}
              onValueChange={(v) => updateField("stoch_rsi_enabled", v)}
              trackColor={{ false: "#30363D", true: "#F0B90B44" }}
              thumbColor={current.stoch_rsi_enabled ? "#F0B90B" : "#8B949E"}
            />
          </View>

          {/* MA20 */}
          <Divider />
          <View style={styles.row}>
            <View style={{ flex: 1 }}>
              <Text style={styles.rowLabel}>فيلتر MA20</Text>
              <Text style={styles.rowHint}>تفعيل فيلتر المتوسط المتحرك 20</Text>
            </View>
            <Switch
              value={current.ma20_enabled}
              onValueChange={(v) => updateField("ma20_enabled", v)}
              trackColor={{ false: "#30363D", true: "#F0B90B44" }}
              thumbColor={current.ma20_enabled ? "#F0B90B" : "#8B949E"}
            />
          </View>
        </View>

        {/* ── الفريم الزمني ── */}
        <SectionHeader title="🕯️ الفريم الزمني" />
        <View style={styles.card}>
          <Text style={[styles.rowLabel, { padding: 14, paddingBottom: 0 }]}>الفريم الزمني للشموع</Text>
          <View style={styles.intervalRow}>
            {INTERVAL_OPTIONS.map((min) => (
              <TouchableOpacity
                key={min}
                style={[
                  styles.intervalBtn,
                  current.interval_minutes === min && styles.intervalBtnActive,
                ]}
                onPress={() => updateField("interval_minutes", min)}
              >
                <Text
                  style={[
                    styles.intervalText,
                    current.interval_minutes === min && styles.intervalTextActive,
                  ]}
                >
                  {min < 60 ? `${min}د` : `${min / 60}س`}
                </Text>
              </TouchableOpacity>
            ))}
          </View>
        </View>

        {/* ── إدارة العملات ── */}
        <SectionHeader title="🪙 إدارة العملات" />
        <View style={styles.card}>
          <View style={[styles.row, { gap: 8 }]}>
            <TextInput
              style={[styles.symbolInput, { flex: 1 }]}
              placeholder="اسم العملة (مثال: ETH)"
              placeholderTextColor="#8B949E"
              value={symbolInput}
              onChangeText={setSymbolInput}
              autoCapitalize="characters"
            />
            <TouchableOpacity
              style={[styles.actionBtn, { backgroundColor: "#238636" }]}
              onPress={handleAddSymbol}
              disabled={symbolLoading}
            >
              {symbolLoading ? (
                <ActivityIndicator color="#fff" size="small" />
              ) : (
                <Text style={styles.actionBtnText}>+ إضافة</Text>
              )}
            </TouchableOpacity>
          </View>

          {symbols.length > 0 && (
            <>
              <Divider />
              <View style={styles.symbolsList}>
                {symbols.map((sym) => (
                  <View key={sym} style={styles.symbolItem}>
                    <Text style={styles.symbolText}>{sym}</Text>
                    <TouchableOpacity
                      style={styles.removeBtn}
                      onPress={() => handleRemoveSymbol(sym)}
                    >
                      <Text style={styles.removeBtnText}>✕</Text>
                    </TouchableOpacity>
                  </View>
                ))}
              </View>
            </>
          )}

          {symbols.length === 0 && (
            <View style={{ padding: 14, paddingTop: 0 }}>
              <Text style={styles.rowHint}>لا توجد عملات مضافة</Text>
            </View>
          )}
        </View>

        {/* ── حفظ ── */}
        {Object.keys(edited).length > 0 && (
          <TouchableOpacity
            style={[styles.saveBtn, saving && styles.saveBtnDisabled]}
            onPress={handleSave}
            disabled={saving}
          >
            {saving ? (
              <ActivityIndicator color="#0D1117" />
            ) : (
              <Text style={styles.saveBtnText}>💾 حفظ الإعدادات</Text>
            )}
          </TouchableOpacity>
        )}

        <TouchableOpacity style={styles.resetBtn} onPress={fetchSettings}>
          <Text style={styles.resetBtnText}>↺ إعادة تحميل الإعدادات</Text>
        </TouchableOpacity>
      </ScrollView>
    </ScreenContainer>
  );
}

function SectionHeader({ title }: { title: string }) {
  return <Text style={styles.sectionTitle}>{title}</Text>;
}

function Divider() {
  return <View style={styles.divider} />;
}

function NumberRow({
  label,
  value,
  onChange,
  hint,
  decimal = false,
}: {
  label: string;
  value: number;
  onChange: (v: number) => void;
  hint?: string;
  decimal?: boolean;
}) {
  const [text, setText] = useState(value?.toString() ?? "");

  useEffect(() => {
    setText(value?.toString() ?? "");
  }, [value]);

  const handleChange = (t: string) => {
    setText(t);
    const num = decimal ? parseFloat(t) : parseInt(t, 10);
    if (!isNaN(num)) onChange(num);
  };

  return (
    <View style={styles.row}>
      <View style={{ flex: 1 }}>
        <Text style={styles.rowLabel}>{label}</Text>
        {hint && <Text style={styles.rowHint}>{hint}</Text>}
      </View>
      <TextInput
        style={styles.numberInput}
        value={text}
        onChangeText={handleChange}
        keyboardType="decimal-pad"
        returnKeyType="done"
      />
    </View>
  );
}

const styles = StyleSheet.create({
  scroll: { flex: 1 },
  content: { padding: 16, paddingBottom: 40, gap: 10 },
  center: { flex: 1, alignItems: "center", justifyContent: "center" },
  pageTitle: { color: "#E6EDF3", fontSize: 22, fontWeight: "bold", marginBottom: 4 },
  sectionTitle: { color: "#8B949E", fontSize: 13, fontWeight: "600", marginTop: 8, marginBottom: 2 },

  card: {
    backgroundColor: "#161B22",
    borderRadius: 14,
    borderWidth: 1,
    borderColor: "#30363D",
    overflow: "hidden",
  },
  row: {
    flexDirection: "row",
    alignItems: "center",
    padding: 14,
    gap: 12,
  },
  rowLabel: { color: "#E6EDF3", fontSize: 14, fontWeight: "500" },
  rowHint: { color: "#8B949E", fontSize: 11, marginTop: 2 },
  divider: { height: 1, backgroundColor: "#30363D", marginHorizontal: 14 },

  numberInput: {
    backgroundColor: "#0D1117",
    borderWidth: 1,
    borderColor: "#30363D",
    borderRadius: 8,
    padding: 8,
    color: "#E6EDF3",
    fontSize: 15,
    minWidth: 80,
    textAlign: "center",
  },

  dualInput: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
  },
  dualField: {
    backgroundColor: "#0D1117",
    borderWidth: 1,
    borderColor: "#30363D",
    borderRadius: 8,
    padding: 8,
    color: "#E6EDF3",
    fontSize: 14,
    width: 60,
    textAlign: "center",
  },
  dualSep: {
    color: "#8B949E",
    fontSize: 16,
    fontWeight: "bold",
  },

  intervalRow: {
    flexDirection: "row",
    gap: 8,
    padding: 14,
    paddingTop: 8,
  },
  intervalBtn: {
    flex: 1,
    paddingVertical: 10,
    alignItems: "center",
    borderRadius: 8,
    borderWidth: 1,
    borderColor: "#30363D",
    backgroundColor: "#0D1117",
  },
  intervalBtnActive: { borderColor: "#F0B90B", backgroundColor: "#F0B90B22" },
  intervalText: { color: "#8B949E", fontWeight: "600" },
  intervalTextActive: { color: "#F0B90B" },

  symbolInput: {
    backgroundColor: "#0D1117",
    borderWidth: 1,
    borderColor: "#30363D",
    borderRadius: 8,
    padding: 10,
    color: "#E6EDF3",
    fontSize: 14,
  },
  actionBtn: {
    borderRadius: 8,
    paddingHorizontal: 16,
    paddingVertical: 10,
    alignItems: "center",
    justifyContent: "center",
    minWidth: 80,
  },
  actionBtnText: { color: "#fff", fontWeight: "bold", fontSize: 13 },

  symbolsList: {
    flexDirection: "row",
    flexWrap: "wrap",
    padding: 14,
    gap: 8,
  },
  symbolItem: {
    flexDirection: "row",
    alignItems: "center",
    backgroundColor: "#0D1117",
    borderRadius: 8,
    borderWidth: 1,
    borderColor: "#30363D",
    paddingHorizontal: 10,
    paddingVertical: 6,
    gap: 6,
  },
  symbolText: { color: "#E6EDF3", fontSize: 13, fontWeight: "600" },
  removeBtn: { padding: 2 },
  removeBtnText: { color: "#FF4444", fontSize: 12, fontWeight: "bold" },

  saveBtn: {
    backgroundColor: "#F0B90B",
    borderRadius: 12,
    padding: 16,
    alignItems: "center",
    marginTop: 8,
  },
  saveBtnDisabled: { opacity: 0.6 },
  saveBtnText: { color: "#0D1117", fontSize: 16, fontWeight: "bold" },

  resetBtn: {
    borderRadius: 12,
    padding: 14,
    alignItems: "center",
    borderWidth: 1,
    borderColor: "#30363D",
  },
  resetBtnText: { color: "#8B949E", fontSize: 14 },
});
