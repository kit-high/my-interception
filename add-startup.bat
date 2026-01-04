@echo off
setlocal
set "TARGET=%~dp0launch-hidden.bat"
set "WORKDIR=%~dp0"
set "STARTUP=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
set "LINK=%STARTUP%\launch-hidden.lnk"

echo Creating startup shortcut for: %TARGET%
if not exist "%TARGET%" (
  echo Target not found. Aborting.
  exit /b 1
)

rem Remove any existing shortcut with the same name
if exist "%LINK%" del "%LINK%"

powershell -NoProfile -ExecutionPolicy Bypass -Command " $ws = New-Object -ComObject WScript.Shell; $lnk = $ws.CreateShortcut('%LINK%'); $lnk.TargetPath = '%TARGET%'; $lnk.WorkingDirectory = '%WORKDIR%'; $lnk.WindowStyle = 1; $lnk.Save() "

if exist "%LINK%" (
  echo Shortcut created: %LINK%
) else (
  echo Failed to create shortcut.
  exit /b 1
)

endlocal
