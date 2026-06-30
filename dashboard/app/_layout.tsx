import { DarkTheme, ThemeProvider } from "@react-navigation/native";
import { Stack, useRouter, useSegments } from "expo-router";
import * as SplashScreen from "expo-splash-screen";
import { StatusBar } from "expo-status-bar";
import { useEffect } from "react";
import { GestureHandlerRootView } from "react-native-gesture-handler";
import { SafeAreaProvider } from "react-native-safe-area-context";
import "../global.css";
import { AuthProvider, useAuth } from "@/lib/auth-context";
import { ThemeProvider as ManusThemeProvider } from "@/lib/theme-provider";

SplashScreen.preventAutoHideAsync();

function RootLayoutNav() {
  const { isAuthenticated, isLoading } = useAuth();
  const router = useRouter();
  const segments = useSegments();

  useEffect(() => {
    if (isLoading) return;
    const inTabsGroup = segments[0] === "(tabs)";
    if (!isAuthenticated && inTabsGroup) {
      router.replace("/login");
    } else if (isAuthenticated && !inTabsGroup) {
      router.replace("/(tabs)");
      import("@/lib/bot-api").then(({ registerPushToken }) => {
        registerPushToken();
      });
    }
  }, [isAuthenticated, isLoading, segments]);

  useEffect(() => {
    SplashScreen.hideAsync();
  }, []);

  return (
    <Stack screenOptions={{ headerShown: false }}>
      <Stack.Screen name="login" options={{ headerShown: false }} />
      <Stack.Screen name="(tabs)" options={{ headerShown: false }} />
    </Stack>
  );
}

export default function RootLayout() {
  return (
    <GestureHandlerRootView style={{ flex: 1 }}>
      <SafeAreaProvider>
        <ManusThemeProvider>
          <ThemeProvider value={DarkTheme}>
            <StatusBar style="light" />
            <AuthProvider>
              <RootLayoutNav />
            </AuthProvider>
          </ThemeProvider>
        </ManusThemeProvider>
      </SafeAreaProvider>
    </GestureHandlerRootView>
  );
}
