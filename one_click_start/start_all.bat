@echo off
title AI Trading Bot — One Click Launcher
color 0A
mode con: cols=60 lines=35

echo.
echo  ============================================
echo   AI TRADING BOT — ONE CLICK LAUNCHER
echo   NSE F^&O Intraday ^ Paper Trading
echo  ============================================
echo.

:: ── Project path ─────────────────────────────────────────
set PROJECT=F:\My AI Project\AlgoTrading\AI_Trading1
set PANEL=%~dp0control_panel.html

:: ── Activate virtual environment if present ───────────────
if exist "%PROJECT%\venv\Scripts\activate.bat" (
    echo [ENV] Activating virtual environment...
    call "%PROJECT%\venv\Scripts\activate.bat"
) else if exist "%PROJECT%\.venv\Scripts\activate.bat" (
    echo [ENV] Activating virtual environment...
    call "%PROJECT%\.venv\Scripts\activate.bat"
) else (
    echo [ENV] No venv found — using global Python
)
echo.

:: ── Step 1: n8n ───────────────────────────────────────────
echo [1/4] Checking n8n on port 5678...
netstat -an 2>nul | findstr ":5678" | findstr "LISTENING" >nul 2>&1
if %errorlevel% equ 0 (
    echo       n8n is already running.
) else (
    echo       n8n not running — starting now...
    where n8n >nul 2>&1
    if %errorlevel% equ 0 (
        start "n8n Workflow Engine" cmd /k "color 0D && echo n8n starting... && n8n start"
    ) else (
        echo       [WARN] n8n not found. Install with: npm install -g n8n
        echo       Skipping n8n...
    )
    timeout /t 6 /nobreak >nul
)
echo.

:: ── Step 2: Webhook server ────────────────────────────────
echo [2/4] Starting AI Webhook Server on port 8000...
start "AI Webhook Server" cmd /k "color 0A && cd /d "%PROJECT%" && echo Starting webhook server... && uvicorn webhook.server:app --host 0.0.0.0 --port 8000 --reload"
timeout /t 4 /nobreak >nul
echo.

:: ── Step 3: Streamlit dashboard ───────────────────────────
echo [3/4] Starting Streamlit Dashboard on port 8501...
start "Streamlit Dashboard" cmd /k "color 0B && cd /d "%PROJECT%" && echo Starting Streamlit... && streamlit run dashboard/app.py"
timeout /t 4 /nobreak >nul
echo.

:: ── Step 4: ngrok tunnel ──────────────────────────────────
echo [4/4] Starting ngrok tunnel on port 8000...
where ngrok >nul 2>&1
if %errorlevel% equ 0 (
    start "ngrok Tunnel" cmd /k "color 0E && echo Starting ngrok... && ngrok http 8000"
    timeout /t 5 /nobreak >nul
) else (
    echo       [WARN] ngrok not found. Download from https://ngrok.com
    echo       Skipping ngrok...
)
echo.

:: ── Open browser windows ──────────────────────────────────
echo Opening Control Panel + Services in browser...
timeout /t 2 /nobreak >nul
start "" "%PANEL%"
timeout /t 1 /nobreak >nul
start "" "http://localhost:8501"
timeout /t 1 /nobreak >nul
start "" "http://localhost:5678"

echo.
echo  ============================================
echo   ALL SERVICES LAUNCHED!
echo.
echo   Webhook:    http://localhost:8000
echo   Dashboard:  http://localhost:8501
echo   n8n:        http://localhost:5678
echo   ngrok UI:   http://localhost:4040
echo   API Docs:   http://localhost:8000/docs
echo  ============================================
echo.
echo  Press any key to close this window.
echo  (All services will keep running in their own windows)
echo.
pause >nul
