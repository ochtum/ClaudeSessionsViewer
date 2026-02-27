@echo off
setlocal
cd /d %~dp0\..\..
start "ClaudeSessionsViewer" cmd /c python viewer.py
timeout /t 1 /nobreak >nul
start "" "http://127.0.0.1:8767/"
echo Launched ClaudeSessionsViewer on http://127.0.0.1:8767
