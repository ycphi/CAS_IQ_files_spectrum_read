@echo off
setlocal
set "PSModulePath=%SystemRoot%\System32\WindowsPowerShell\v1.0\Modules"
echo IQCS16 Spectrum - Offline Local Installer
echo.
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1"
set "installResult=%ERRORLEVEL%"
echo.
if "%installResult%"=="0" (
    echo Installation complete. Double-click IQCS16_spectrum.exe in this folder.
) else (
    echo Installation failed. See the logs folder for details.
)
if /I not "%~1"=="--no-pause" pause
exit /b %installResult%
