# Crypto Bot Dashboard — TODO

## Setup & Design
- [x] تهيئة مشروع Expo
- [x] كتابة ملف التصميم design.md
- [x] توليد شعار التطبيق
- [x] تحديث theme.config.js بألوان العملات الرقمية (Binance yellow + dark)
- [x] تحديث app.config.ts باسم التطبيق

## Core Infrastructure
- [x] إنشاء API client للتواصل مع Flask backend (lib/bot-api.ts)
- [x] إنشاء Auth context لإدارة الجلسة وبيانات الدخول
- [x] حفظ URL السيرفر وبيانات الدخول في AsyncStorage

## Screens
- [x] شاشة تسجيل الدخول (login.tsx)
- [x] شاشة Dashboard الرئيسية (index.tsx)
- [x] شاشة الصفقات - Open + History (trades.tsx)
- [x] شاشة الإعدادات (settings.tsx)
- [x] شاشة الاتصال/الملف الشخصي (profile.tsx)

## Components
- [ ] BotStatusCard — حالة البوت + زر تشغيل/إيقاف
- [ ] BalanceCard — رصيد USDT
- [ ] StatsRow — PnL + Win Rate + Trades Count
- [ ] CircuitBreakerBanner — تنبيه Circuit Breaker
- [ ] TradeCard — بطاقة صفقة مفتوحة
- [ ] HistoryItem — عنصر سجل الصفقات
- [ ] SettingsRow — صف إعداد قابل للتعديل

## Navigation
- [x] إعداد Tab Navigation (Dashboard, Trades, Settings, Profile)
- [x] إعداد Auth flow (redirect to login if not authenticated)

## Auto-refresh
- [x] تحديث تلقائي كل 10 ثوانٍ للـ Dashboard
- [x] Pull-to-refresh في جميع الشاشات

## Branding
- [ ] تحديث icon.png بالشعار الجديد
- [ ] تحديث splash-icon.png
