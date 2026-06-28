# Crypto Bot Dashboard — Design Document

## Overview
تطبيق جوال لمراقبة والتحكم في بوت تداول العملات الرقمية RSI Auto Trader على Binance.
يتصل بـ Flask API الخاص بالبوت ويعرض البيانات في الوقت الفعلي.

---

## Color Palette (Dark-first theme)
- **Background**: `#0D1117` (GitHub dark, professional crypto feel)
- **Surface**: `#161B22` (cards/panels)
- **Primary**: `#F0B90B` (Binance yellow — brand recognition)
- **Success**: `#00C087` (green — profit/buy)
- **Error**: `#FF4D4F` (red — loss/stop)
- **Warning**: `#F59E0B` (amber — circuit breaker)
- **Foreground**: `#E6EDF3` (primary text)
- **Muted**: `#8B949E` (secondary text)
- **Border**: `#30363D` (dividers)

---

## Screen List

### 1. Login Screen (`/login`)
- حقل URL السيرفر (قابل للتعديل)
- حقل اسم المستخدم وكلمة المرور
- زر تسجيل الدخول
- حفظ بيانات الدخول في SecureStore

### 2. Dashboard Screen (`/(tabs)/index`)
- **Header**: اسم التطبيق + مؤشر حالة الاتصال
- **Bot Status Card**: حالة البوت (شغال/متوقف) + زر تشغيل/إيقاف
- **Balance Card**: رصيد USDT المتاح
- **Stats Row**: إجمالي PnL + نسبة الفوز + عدد الصفقات
- **Circuit Breaker Banner**: تنبيه إذا كان Circuit Breaker نشطاً
- **Open Trades**: قائمة الصفقات المفتوحة مع PnL لحظي
- **Watch List**: عدد العملات تحت المراقبة

### 3. Trades Screen (`/(tabs)/trades`)
- **Open Trades Tab**: الصفقات المفتوحة مع:
  - اسم العملة + سعر الدخول
  - السعر الحالي + PnL%
  - حالة Trailing Stop
  - سعر Stop Loss
- **History Tab**: سجل الصفقات المغلقة (today/week/all)
  - فلتر زمني
  - كل صفقة: العملة، الدخول، الخروج، الربح/الخسارة، السبب

### 4. Settings Screen (`/(tabs)/settings`)
- **Trading Parameters**:
  - حجم الصفقة (TRADE_AMOUNT)
  - أقصى عدد صفقات (MAX_TRADES)
  - Trailing Stop % (TRAIL_PCT)
  - Stop Loss % (STOP_LOSS_PCT)
- **RSI Settings**:
  - RSI Watch Low/High
  - الفريم الزمني (15/30/60/240 دقيقة)
  - تفعيل/تعطيل MA20
- **Strategy**: RSI / Stochastic RSI
- **Connection**: تعديل URL السيرفر

### 5. Profile/Connection Screen (`/(tabs)/profile`)
- معلومات الاتصال بالسيرفر
- زر تسجيل الخروج
- معلومات البوت (عدد العملات المراقبة)

---

## Key User Flows

### Flow 1: تسجيل الدخول
`Login Screen → إدخال URL + بيانات → POST /api/login → حفظ session → Dashboard`

### Flow 2: مراقبة البوت
`Dashboard → يتحدث كل 10 ثوانٍ → GET /api/status → تحديث الأرقام`

### Flow 3: تشغيل/إيقاف البوت
`Dashboard → ضغط زر Toggle → POST /api/control → تحديث الحالة`

### Flow 4: مراجعة الصفقات
`Trades Tab → Open/History → فلتر → عرض التفاصيل`

### Flow 5: تعديل الإعدادات
`Settings Tab → تعديل قيمة → POST /api/settings → تأكيد`

---

## Layout Principles
- **Dark theme افتراضي** — مناسب لتداول العملات الرقمية
- **بطاقات (Cards)** لعرض المعلومات بشكل منظم
- **ألوان ترميزية**: أخضر للربح، أحمر للخسارة
- **تحديث تلقائي** كل 10 ثوانٍ للبيانات الحية
- **Pull-to-refresh** لتحديث يدوي
