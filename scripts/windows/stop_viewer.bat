@echo off
for /f "tokens=5" %%a in ('netstat -ano ^| findstr :8767 ^| findstr LISTENING') do (
  taskkill /PID %%a /F >nul 2>&1
)
echo Stopped listeners on port 8767 (if any).

