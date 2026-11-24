@echo off
rem Construye "Chatbot Mensura.exe" en la raiz de la carpeta.
rem Usa el compilador de C# que trae .NET Framework 4 en cualquier Windows: nada que instalar.
setlocal
cd /d "%~dp0"
set "CSC=%WINDIR%\Microsoft.NET\Framework64\v4.0.30319\csc.exe"
if not exist "%CSC%" set "CSC=%WINDIR%\Microsoft.NET\Framework\v4.0.30319\csc.exe"
if not exist "%CSC%" (
  echo   No encuentro csc.exe de .NET Framework 4.
  exit /b 1
)
"%CSC%" /nologo /target:winexe /optimize+ /win32icon:icono.ico ^
  /out:"..\Chatbot Mensura.exe" ^
  /r:System.Windows.Forms.dll /r:System.Drawing.dll ChatbotMensura.cs
if errorlevel 1 (
  echo   La compilacion fallo.
  exit /b 1
)
echo   Listo: Chatbot Mensura.exe
