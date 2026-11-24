@echo off
rem Chatbot Mensura - instalacion en un PC nuevo (o tras clonar el repositorio).
rem Deja TODO dentro de esta carpeta: Python, librerias, llama.cpp y los modelos.
rem No instala nada en Windows. Se puede repetir: solo baja lo que falte.
setlocal
cd /d "%~dp0"
if not exist "runtime\python\python.exe" (
  echo.
  echo   Descargando Python 3.14.6 de python.org ^(12 MB^)...
  if not exist "runtime" mkdir runtime
  powershell -NoProfile -ExecutionPolicy Bypass -Command "$ProgressPreference='SilentlyContinue'; Invoke-WebRequest -Uri 'https://www.python.org/ftp/python/3.14.6/python-3.14.6-embed-amd64.zip' -OutFile 'runtime\python.zip'; Expand-Archive -Path 'runtime\python.zip' -DestinationPath 'runtime\python' -Force; Remove-Item 'runtime\python.zip'"
  rem El Python "embeddable" ignora PYTHONPATH: sus rutas van en este archivo.
  (echo python314.zip& echo .& echo ..\..& echo ..\..\libs& echo import site)> "runtime\python\python314._pth"
)
if not exist "runtime\python\python.exe" (
  echo   No se pudo descargar Python. Revisa la conexion a internet.
  pause
  exit /b 1
)
set "PYTHONIOENCODING=utf-8"
set "PYTHONPATH=%~dp0libs"
"runtime\python\python.exe" herramientas\instalar.py %*
rem El programa de Windows: se compila aqui con el csc.exe que trae .NET Framework 4.
if not exist "Chatbot Mensura.exe" call "%~dp0escritorio\construir.bat"
echo.
echo   Para usarlo: doble clic en "Chatbot Mensura.exe"
pause
