@echo off
setlocal

cd /d "%~dp0"

set "GAME_URL=http://127.0.0.1:8000/"
set "SERVER_SCRIPT=%~dp0zombie_defense_web.py"
set "PY_EXE=C:\Users\Admin\AppData\Local\Python\pythoncore-3.14-64\python.exe"

if not exist "%SERVER_SCRIPT%" (
  echo Cannot find zombie_defense_web.py in "%~dp0".
  pause
  exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -Command "try { Invoke-WebRequest -UseBasicParsing -Uri '%GAME_URL%' -TimeoutSec 1 | Out-Null; exit 0 } catch { exit 1 }"
if errorlevel 1 (
  if exist "%PY_EXE%" (
    start "Zombie Defense Server" /D "%~dp0" /min "%PY_EXE%" "%SERVER_SCRIPT%"
  ) else (
    start "Zombie Defense Server" /D "%~dp0" /min py -3 "%SERVER_SCRIPT%"
  )

  powershell -NoProfile -ExecutionPolicy Bypass -Command "$url='%GAME_URL%'; for ($i = 0; $i -lt 30; $i++) { try { Invoke-WebRequest -UseBasicParsing -Uri $url -TimeoutSec 1 | Out-Null; exit 0 } catch { Start-Sleep -Milliseconds 500 } }; exit 1"
  if errorlevel 1 (
    echo Server did not start. Check the "Zombie Defense Server" window.
    pause
    exit /b 1
  )
)

start "" "%GAME_URL%"
exit /b 0
