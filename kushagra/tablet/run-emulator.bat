@echo off
rem Double-click: starts the Pixel_Tablet emulator (if it is not already running), waits until it has
rem booted, then runs run-tablet.bat, which builds and installs the app and opens the Clench Board.
rem
rem   run-emulator.bat            the emulator, on the test servers (Core 8001, web 5174)
rem   run-emulator.bat demo       the same, on the demo servers (Core 8000, web 5173)
rem
rem Needs the Pixel_Tablet AVD from Android Studio's Device Manager. The eye-tracking SDK does not run
rem on an emulator, so the eye-tracking screens will not work there; the layouts and screenshots will.

setlocal EnableExtensions
set "AVD=Pixel_Tablet"
set "SDK=%LOCALAPPDATA%\Android\Sdk"
set "EMU=%SDK%\emulator\emulator.exe"
set "ADB=%SDK%\platform-tools\adb.exe"
set "TABLET_DIR=%~dp0"

if not exist "%EMU%" (echo [x] Emulator not found at %EMU%. Install it from Android Studio's SDK Manager. & goto :fail)
if not exist "%ADB%" (echo [x] adb not found at %ADB%. Install platform-tools from the SDK Manager. & goto :fail)

rem --- start the emulator if it is not already running -------------------------------------------
"%ADB%" devices | findstr /b /c:"emulator-" >nul 2>&1
if errorlevel 1 (
    echo [..] Starting the %AVD% emulator in a new window...
    start "Emulator %AVD%" "%EMU%" -avd %AVD%
) else (
    echo [ok] An emulator is already running.
)

rem --- wait for Android to finish booting --------------------------------------------------------
echo [..] Waiting for the tablet to boot (the first start can take a few minutes)...
set /a TRIES=0
:wait_device
"%ADB%" wait-for-device >nul 2>&1
"%ADB%" shell getprop sys.boot_completed 2>nul | findstr /c:"1" >nul 2>&1
if not errorlevel 1 goto :booted
set /a TRIES+=1
if %TRIES% geq 300 (echo [x] The emulator did not finish booting in 5 minutes. & goto :fail)
timeout /t 1 /nobreak >nul
goto :wait_device
:booted
echo [ok] Tablet emulator is booted.

rem --- build, install, connect and open the app --------------------------------------------------
if /i "%~1"=="demo" (
    call "%TABLET_DIR%run-tablet.bat"
) else (
    call "%TABLET_DIR%run-tablet.bat" test
)
exit /b %errorlevel%

:fail
echo.
pause
exit /b 1
