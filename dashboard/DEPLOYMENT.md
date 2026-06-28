# Crypto Bot Dashboard — Deployment Guide

## تطبيق لوحة تحكم بوت التداول

هذا التطبيق يتصل بـ Flask API الخاص ببوت RSI Auto Trader ويوفر لوحة تحكم احترافية.

## المتطلبات

- Node.js 22+
- pnpm

## التثبيت والتشغيل المحلي

```bash
# تثبيت المكتبات
pnpm install

# تشغيل في وضع التطوير
pnpm run dev

# البناء للإنتاج
pnpm run build

# تشغيل الإنتاج
pnpm run start
```

## التشغيل على Docker

```bash
# البناء
docker build -f Dockerfile.web -t crypto-bot-dashboard .

# التشغيل
docker run -p 3000:8081 -e EXPO_PORT=8081 crypto-bot-dashboard
```

## المتغيرات البيئية

```env
EXPO_PORT=8081  # Port للتطبيق
```

## الشاشات الرئيسية

1. **تسجيل الدخول** — إدخال URL السيرفر + بيانات الدخول
2. **لوحة التحكم** — حالة البوت، الرصيد، الصفقات المفتوحة
3. **الصفقات** — الصفقات المفتوحة والسجل
4. **الإعدادات** — تعديل معاملات التداول
5. **الاتصال** — معلومات السيرفر والـ API

## الاتصال بـ Flask API

التطبيق يتصل بـ Flask API على المسارات التالية:

- `POST /api/login` — تسجيل الدخول
- `GET /api/status` — حالة البوت
- `GET /api/trades` — الصفقات المفتوحة
- `GET /api/history` — سجل الصفقات
- `GET /api/settings` — الإعدادات
- `POST /api/settings` — تحديث الإعدادات
- `POST /api/control` — تشغيل/إيقاف البوت

## الترخيص

MIT
