@echo off
rem Chatbot Mensura - arranca el servidor (pantalla en http://localhost:8013/).
rem Todo lo que usa esta dentro de esta carpeta: Python (runtime), librerias (libs),
rem modelo local (motor_local) y normativa (corpus). No depende de ninguna otra carpeta.
setlocal
cd /d "%~dp0"
set "PYTHONPATH=%~dp0libs"
set "PYTHONIOENCODING=utf-8"
set "PY=%~dp0runtime\python\python.exe"
if not exist "%PY%" (
  echo.
  echo   Falta runtime\python. Ejecuta primero INSTALAR.bat
  echo.
  pause
  exit /b 1
)
echo.
echo   Chatbot Mensura
echo   En este PC:      http://localhost:8013/
echo   Desde la red:    http://%COMPUTERNAME%:8013/
echo   (cerrar esta ventana apaga el chatbot)
echo.
"%PY%" iniciar.py %*
pause
