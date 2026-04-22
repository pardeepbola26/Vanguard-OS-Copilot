# Vanguard OS - Quick Start Guide

## 🚀 How to Start the Server

### Step 1: Navigate to the project folder
```bash
cd /Users/pardeepbola/vanguard-os
```

### Step 2: Start the server
```bash
python3 api_server.py
```

**What you should see:**
```
INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
INFO:     Started reloader process [XXXXX] using StatReload
INFO:     Started server process [XXXXX]
INFO:     Waiting for application startup.
INFO:     Application startup complete.
```

### Step 3: Open the UI in your browser
Open this file in Chrome/Safari:
```
/Users/pardeepbola/vanguard-os/vanguard.html
```
(Right-click → Open With → Browser)

OR just double-click `vanguard.html` in Finder.

---

## 🛑 How to Stop the Server

Press `CTRL+C` in the terminal where the server is running.

---

## 🔧 Troubleshooting

### "Address already in use" error?
Kill existing processes:
```bash
lsof -i :8000
# Note the PID numbers, then:
kill -9 <PID>
```

### "Module not found" error?
Install dependencies:
```bash
python3 -m pip install fastapi uvicorn python-dotenv openai
```

### Still not working?
Check the browser console (Cmd+Option+J) for errors.
