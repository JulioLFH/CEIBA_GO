@echo off
rem Inicia Ceiba GO en el puerto 8040 (acepta conexiones de la red local)
cd /d "%~dp0"
title Ceiba GO
if not exist ".venv\Scripts\python.exe" (
  echo Creando entorno .venv ...
  python -m venv .venv || goto :fin
  .venv\Scripts\pip install -r requirements.txt || goto :fin
)
netstat -ano | findstr /r /c:":8040 .*LISTENING" >nul
if not errorlevel 1 (
  echo Ceiba GO ya esta abierto en el puerto 8040.
  start "" http://127.0.0.1:8040
  goto :fin
)
.venv\Scripts\python.exe manage.py migrate --no-input || goto :fin
.venv\Scripts\python.exe manage.py collectstatic --no-input >nul || goto :fin
start "" /b powershell -NoProfile -WindowStyle Hidden -Command "for($i=0;$i -lt 60;$i++){try{Invoke-WebRequest http://127.0.0.1:8040/ -UseBasicParsing -TimeoutSec 2 | Out-Null; Start-Process http://127.0.0.1:8040; break}catch{Start-Sleep 1}}"
.venv\Scripts\python.exe servir.py
:fin
pause
