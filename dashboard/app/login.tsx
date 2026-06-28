import React, { useState } from "react";
import {
  View,
  Text,
  TextInput,
  TouchableOpacity,
  StyleSheet,
  KeyboardAvoidingView,
  Platform,
  ActivityIndicator,
  Alert,
  ScrollView,
} from "react-native";
import { StatusBar } from "expo-status-bar";
import { useAuth } from "@/lib/auth-context";
import { login, setServerUrl as saveServerUrl } from "@/lib/bot-api";
import { SafeAreaView } from "react-native-safe-area-context";

export default function LoginScreen() {
  const { setAuthenticated, setServerUrl } = useAuth();
  const [serverUrl, setServerUrlLocal] = useState("http://");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);

  const handleLogin = async () => {
    if (!serverUrl.startsWith("http")) {
      Alert.alert("خطأ", "يرجى إدخال عنوان السيرفر بشكل صحيح (يبدأ بـ http:// أو https://)");
      return;
    }
    if (!username || !password) {
      Alert.alert("خطأ", "يرجى إدخال اسم المستخدم وكلمة المرور");
      return;
    }
    setLoading(true);
    try {
      await saveServerUrl(serverUrl);
      setServerUrl(serverUrl);
      const result = await login(username, password);
      if (result.ok) {
        setAuthenticated(true);
      } else {
        Alert.alert("فشل تسجيل الدخول", result.error || "بيانات غير صحيحة");
      }
    } catch (e: any) {
      Alert.alert("خطأ", e.message || "تعذر الاتصال بالسيرفر");
    } finally {
      setLoading(false);
    }
  };

  return (
    <SafeAreaView style={styles.container}>
      <StatusBar style="light" />
      <KeyboardAvoidingView
        behavior={Platform.OS === "ios" ? "padding" : "height"}
        style={styles.keyboardView}
      >
        <ScrollView contentContainerStyle={styles.scrollContent} keyboardShouldPersistTaps="handled">
          {/* Header */}
          <View style={styles.header}>
            <Text style={styles.botIcon}>🤖</Text>
            <Text style={styles.title}>Crypto Bot</Text>
            <Text style={styles.subtitle}>لوحة تحكم بوت التداول</Text>
          </View>

          {/* Form */}
          <View style={styles.form}>
            <View style={styles.inputGroup}>
              <Text style={styles.label}>عنوان السيرفر</Text>
              <TextInput
                style={styles.input}
                value={serverUrl}
                onChangeText={setServerUrlLocal}
                placeholder="http://your-server:5000"
                placeholderTextColor="#8B949E"
                autoCapitalize="none"
                autoCorrect={false}
                keyboardType="url"
                returnKeyType="next"
              />
            </View>

            <View style={styles.inputGroup}>
              <Text style={styles.label}>اسم المستخدم</Text>
              <TextInput
                style={styles.input}
                value={username}
                onChangeText={setUsername}
                placeholder="admin"
                placeholderTextColor="#8B949E"
                autoCapitalize="none"
                autoCorrect={false}
                returnKeyType="next"
              />
            </View>

            <View style={styles.inputGroup}>
              <Text style={styles.label}>كلمة المرور</Text>
              <TextInput
                style={styles.input}
                value={password}
                onChangeText={setPassword}
                placeholder="••••••••"
                placeholderTextColor="#8B949E"
                secureTextEntry
                returnKeyType="done"
                onSubmitEditing={handleLogin}
              />
            </View>

            <TouchableOpacity
              style={[styles.loginBtn, loading && styles.loginBtnDisabled]}
              onPress={handleLogin}
              disabled={loading}
              activeOpacity={0.8}
            >
              {loading ? (
                <ActivityIndicator color="#0D1117" />
              ) : (
                <Text style={styles.loginBtnText}>تسجيل الدخول</Text>
              )}
            </TouchableOpacity>
          </View>

          <Text style={styles.hint}>
            تأكد من تشغيل البوت على السيرفر وتفعيل DASHBOARD_USERNAME و DASHBOARD_PASSWORD
          </Text>
        </ScrollView>
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: "#0D1117",
  },
  keyboardView: {
    flex: 1,
  },
  scrollContent: {
    flexGrow: 1,
    justifyContent: "center",
    padding: 24,
  },
  header: {
    alignItems: "center",
    marginBottom: 40,
  },
  botIcon: {
    fontSize: 64,
    marginBottom: 12,
  },
  title: {
    fontSize: 32,
    fontWeight: "bold",
    color: "#F0B90B",
    letterSpacing: 1,
  },
  subtitle: {
    fontSize: 14,
    color: "#8B949E",
    marginTop: 4,
  },
  form: {
    backgroundColor: "#161B22",
    borderRadius: 16,
    padding: 20,
    borderWidth: 1,
    borderColor: "#30363D",
    gap: 16,
  },
  inputGroup: {
    gap: 6,
  },
  label: {
    color: "#8B949E",
    fontSize: 13,
    fontWeight: "500",
  },
  input: {
    backgroundColor: "#0D1117",
    borderWidth: 1,
    borderColor: "#30363D",
    borderRadius: 10,
    padding: 14,
    color: "#E6EDF3",
    fontSize: 15,
    textAlign: "left",
  },
  loginBtn: {
    backgroundColor: "#F0B90B",
    borderRadius: 12,
    padding: 16,
    alignItems: "center",
    marginTop: 4,
  },
  loginBtnDisabled: {
    opacity: 0.6,
  },
  loginBtnText: {
    color: "#0D1117",
    fontSize: 16,
    fontWeight: "bold",
  },
  hint: {
    color: "#8B949E",
    fontSize: 12,
    textAlign: "center",
    marginTop: 24,
    lineHeight: 18,
  },
});
