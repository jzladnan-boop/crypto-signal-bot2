import React, { useState } from "react";
import {
  View,
  Text,
  StyleSheet,
  ScrollView,
  TouchableOpacity,
  TextInput,
  Alert,
  ActivityIndicator,
} from "react-native";
import { ScreenContainer } from "@/components/screen-container";
import { useAuth } from "@/lib/auth-context";
import { setServerUrl as saveServerUrl, getServerUrl } from "@/lib/bot-api";
import { useEffect } from "react";

export default function ProfileScreen() {
  const { logout, serverUrl, setServerUrl } = useAuth();
  const [newUrl, setNewUrl] = useState(serverUrl);
  const [saving, setSaving] = useState(false);
  const [loggingOut, setLoggingOut] = useState(false);

  useEffect(() => {
    getServerUrl().then((url) => {
      setNewUrl(url);
    });
  }, []);

  const handleSaveUrl = async () => {
    if (!newUrl.startsWith("http")) {
      Alert.alert("خطأ", "يرجى إدخال عنوان صحيح يبدأ بـ http:// أو https://");
      return;
    }
    setSaving(true);
    await saveServerUrl(newUrl);
    setServerUrl(newUrl);
    setSaving(false);
    Alert.alert("✅ تم الحفظ", "تم تحديث عنوان السيرفر");
  };

  const handleLogout = () => {
    Alert.alert(
      "تسجيل الخروج",
      "هل تريد تسجيل الخروج؟",
      [
        { text: "إلغاء", style: "cancel" },
        {
          text: "خروج",
          style: "destructive",
          onPress: async () => {
            setLoggingOut(true);
            await logout();
            setLoggingOut(false);
          },
        },
      ]
    );
  };

  return (
    <ScreenContainer>
      <ScrollView
        style={styles.scroll}
        contentContainerStyle={styles.content}
        showsVerticalScrollIndicator={false}
      >
        {/* Bot Avatar */}
        <View style={styles.avatarSection}>
          <View style={styles.avatar}>
            <Text style={styles.avatarIcon}>🤖</Text>
          </View>
          <Text style={styles.botName}>Crypto Bot</Text>
          <Text style={styles.botSub}>RSI Auto Trader — Binance</Text>
        </View>

        {/* Connection Info */}
        <Text style={styles.sectionTitle}>🔗 إعدادات الاتصال</Text>
        <View style={styles.card}>
          <Text style={styles.fieldLabel}>عنوان السيرفر (URL)</Text>
          <TextInput
            style={styles.urlInput}
            value={newUrl}
            onChangeText={setNewUrl}
            placeholder="http://your-server:5000"
            placeholderTextColor="#8B949E"
            autoCapitalize="none"
            autoCorrect={false}
            keyboardType="url"
            returnKeyType="done"
          />
          <TouchableOpacity
            style={[styles.saveBtn, saving && styles.saveBtnDisabled]}
            onPress={handleSaveUrl}
            disabled={saving}
          >
            {saving ? (
              <ActivityIndicator color="#0D1117" size="small" />
            ) : (
              <Text style={styles.saveBtnText}>حفظ العنوان</Text>
            )}
          </TouchableOpacity>
        </View>

        {/* Bot Info */}
        <Text style={styles.sectionTitle}>ℹ️ معلومات البوت</Text>
        <View style={styles.card}>
          <InfoRow label="المنصة" value="Binance Spot" />
          <Divider />
          <InfoRow label="الاستراتيجية" value="RSI / Stochastic RSI" />
          <Divider />
          <InfoRow label="الفريم الافتراضي" value="30 دقيقة" />
          <Divider />
          <InfoRow label="الحماية" value="Circuit Breaker (3 خسائر)" />
        </View>

        {/* API Endpoints */}
        <Text style={styles.sectionTitle}>📡 نقاط API المتاحة</Text>
        <View style={styles.card}>
          <ApiRow method="GET" path="/api/status" desc="حالة البوت" />
          <Divider />
          <ApiRow method="GET" path="/api/trades" desc="الصفقات المفتوحة" />
          <Divider />
          <ApiRow method="GET" path="/api/history" desc="سجل الصفقات" />
          <Divider />
          <ApiRow method="GET" path="/api/settings" desc="الإعدادات" />
          <Divider />
          <ApiRow method="POST" path="/api/control" desc="تشغيل/إيقاف" />
        </View>

        {/* Logout */}
        <TouchableOpacity
          style={[styles.logoutBtn, loggingOut && styles.logoutBtnDisabled]}
          onPress={handleLogout}
          disabled={loggingOut}
        >
          {loggingOut ? (
            <ActivityIndicator color="#FF4D4F" size="small" />
          ) : (
            <Text style={styles.logoutText}>🚪 تسجيل الخروج</Text>
          )}
        </TouchableOpacity>
      </ScrollView>
    </ScreenContainer>
  );
}

function InfoRow({ label, value }: { label: string; value: string }) {
  return (
    <View style={styles.infoRow}>
      <Text style={styles.infoLabel}>{label}</Text>
      <Text style={styles.infoValue}>{value}</Text>
    </View>
  );
}

function ApiRow({ method, path, desc }: { method: string; path: string; desc: string }) {
  const methodColor = method === "GET" ? "#00C087" : "#F0B90B";
  return (
    <View style={styles.apiRow}>
      <View style={[styles.methodBadge, { backgroundColor: methodColor + "22", borderColor: methodColor }]}>
        <Text style={[styles.methodText, { color: methodColor }]}>{method}</Text>
      </View>
      <View style={{ flex: 1 }}>
        <Text style={styles.apiPath}>{path}</Text>
        <Text style={styles.apiDesc}>{desc}</Text>
      </View>
    </View>
  );
}

function Divider() {
  return <View style={styles.divider} />;
}

const styles = StyleSheet.create({
  scroll: { flex: 1 },
  content: { padding: 16, paddingBottom: 40, gap: 12 },

  avatarSection: { alignItems: "center", paddingVertical: 20, gap: 8 },
  avatar: {
    width: 80,
    height: 80,
    borderRadius: 40,
    backgroundColor: "#161B22",
    borderWidth: 2,
    borderColor: "#F0B90B",
    alignItems: "center",
    justifyContent: "center",
  },
  avatarIcon: { fontSize: 40 },
  botName: { color: "#E6EDF3", fontSize: 22, fontWeight: "bold" },
  botSub: { color: "#8B949E", fontSize: 13 },

  sectionTitle: { color: "#8B949E", fontSize: 13, fontWeight: "600" },

  card: {
    backgroundColor: "#161B22",
    borderRadius: 14,
    borderWidth: 1,
    borderColor: "#30363D",
    overflow: "hidden",
  },
  fieldLabel: { color: "#8B949E", fontSize: 12, padding: 14, paddingBottom: 6 },
  urlInput: {
    backgroundColor: "#0D1117",
    marginHorizontal: 14,
    borderWidth: 1,
    borderColor: "#30363D",
    borderRadius: 8,
    padding: 12,
    color: "#E6EDF3",
    fontSize: 14,
  },
  saveBtn: {
    margin: 14,
    marginTop: 10,
    backgroundColor: "#F0B90B",
    borderRadius: 8,
    padding: 12,
    alignItems: "center",
  },
  saveBtnDisabled: { opacity: 0.6 },
  saveBtnText: { color: "#0D1117", fontWeight: "bold" },

  infoRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    padding: 14,
  },
  infoLabel: { color: "#8B949E", fontSize: 13 },
  infoValue: { color: "#E6EDF3", fontSize: 13, fontWeight: "500" },
  divider: { height: 1, backgroundColor: "#30363D", marginHorizontal: 14 },

  apiRow: {
    flexDirection: "row",
    alignItems: "center",
    padding: 12,
    gap: 10,
  },
  methodBadge: {
    borderWidth: 1,
    borderRadius: 4,
    paddingHorizontal: 6,
    paddingVertical: 2,
    minWidth: 44,
    alignItems: "center",
  },
  methodText: { fontSize: 11, fontWeight: "bold" },
  apiPath: { color: "#E6EDF3", fontSize: 12, fontFamily: "monospace" },
  apiDesc: { color: "#8B949E", fontSize: 11, marginTop: 1 },

  logoutBtn: {
    borderRadius: 12,
    padding: 16,
    alignItems: "center",
    borderWidth: 1,
    borderColor: "#FF4D4F44",
    backgroundColor: "#FF4D4F11",
    marginTop: 8,
  },
  logoutBtnDisabled: { opacity: 0.6 },
  logoutText: { color: "#FF4D4F", fontSize: 15, fontWeight: "bold" },
});
