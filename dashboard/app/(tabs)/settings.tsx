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
import { getSettings, updateSettings, BotSettings } from "@/lib/bot-api";
import { useAuth } from "@/lib/auth-context";

const INTERVAL_OPTIONS = [15, 30, 60, 240];

export default function SettingsScreen() {
  const { logout } = useAuth();
  const [settings, setSettings] = useState<BotSettings | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [edited, setEdited] = useState<Partial<BotSettings>>({});
  const [strategy, setStrategy] = useState<"rsi" | "stoch_rsi">("rsi");

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

  useEffect(() => {
    fetchSettings();
  }, [fetchSettings]);

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

        {/* Trading Parameters */}
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
        </View>

        {/* RSI Settings */}
        <SectionHeader title="📊 إعدادات RSI" />

        <View style={styles.card}>
          <NumberRow
            label="RSI Watch Low"
            value={current.rsi_low}
            onChange={(v) => updateField("rsi_low", v)}
            hint="الحد الأدنى لمنطقة المراقبة"
            decimal
          />
          <Divider />
          <NumberRow
            label="RSI Watch High"
            value={current.rsi_high}
            onChange={(v) => updateField("rsi_high", v)}
            hint="الحد الأعلى لمنطقة المراقبة"
            decimal
          />
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

        {/* Interval */}
        <SectionHeader title="🕯️ الفريم الزمني" />
        <View style={styles.card}>
          <Text style={styles.rowLabel}>الفريم الزمني للشموع</Text>
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

        {/* Save Button */}
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
