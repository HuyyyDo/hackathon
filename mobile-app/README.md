# Mobile App (iOS + Android)

This is an Expo React Native mobile client for the existing backend.

## 1) Start backend first
From project root:

```powershell
.\.venv\Scripts\Activate.ps1
python -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

## 2) Run mobile app
In a new terminal:

```powershell
cd mobile-app
npm install
npm run start
```

Then open using:
- iOS Simulator (`i` in Expo terminal)
- Android Emulator (`a` in Expo terminal)
- Expo Go app (scan QR)

## API base URL
Default is set in `app.json`:
- `http://127.0.0.1:8000`

For Android emulator, `App.js` automatically falls back to:
- `http://10.0.2.2:8000`

For physical phone, set `expo.extra.API_BASE_URL` in `app.json` to your PC LAN IP, for example:
- `http://192.168.1.20:8000`

## Features included
- Chat send/receive (`/api/chat`)
- Quick actions for Form 1/2/3/4 + reset
- Active Form Data panel
