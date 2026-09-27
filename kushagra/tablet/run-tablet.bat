@echo off
rem One-click tablet run: starts the Core and the web board on this laptop, installs the latest
rem build of the tablet app on the plugged-in tablet, connects the tablet to the board and opens it.
rem
rem   run-tablet.bat              open Clench Board (Eyedid + web board) if this branch has it
rem   run-tablet.bat mediapipe    open the standalone MediaPipe app instead (no servers needed)
rem
rem Ports: the Core on 8001 and the web board on 5174 (the demo ports 8000 / 5173 stay free). The
rem tablet still loads http://localhost:5173/, which adb reverse forwards to 5174 here.
rem Run it again any time: servers that are already up are left alone; the app is rebuilt.

setlocal EnableExtensions
set "CORE_PORT=8001"
set "WEB_PORT=5174"
set "TABLET_PORT=5173"
set "PKG=com.clench.eyetrack"

set "TABLET_DIR=%~dp0"
pushd "%TABLET_DIR%..\.."
set "REPO=%CD%"
popd

rem --- adb -------------------------------------------------------------------------------------
set "ADB=%LOCALAPPDATA%\Android\Sdk\platform-tools\adb.exe"
if not exist "%ADB%" (
    where adb >nul 2>&1 || (echo [x] adb not found. Install Android SDK platform-tools. & goto :fail)
    set "ADB=adb"
)
"%ADB%" get-state >nul 2>&1
if errorlevel 1 (
    echo [x] No tablet found. Plug it in over USB, turn on USB debugging and accept the prompt on the tablet.
    goto :fail
)
echo [ok] Tablet connected.

rem --- which screen to open --------------------------------------------------------------------
set "ACTIVITY=.MainActivity"
set "NEED_SERVERS=0"
if /i not "%~1"=="mediapipe" (
    findstr /c:".board.BoardActivity" "%TABLET_DIR%app\src\main\AndroidManifest.xml" >nul 2>&1
    if not errorlevel 1 (
        set "ACTIVITY=.board.BoardActivity"
        set "NEED_SERVERS=1"
    ) else (
        echo [i] This branch has no Clench Board; opening the MediaPipe app.
    )
)

rem --- servers ---------------------------------------------------------------------------------
if "%NEED_SERVERS%"=="1" (
    if not exist "%REPO%\web\node_modules" (
        echo [..] Installing web packages, first time only...
        call npm --prefix "%REPO%\web" ci || goto :fail
    )

    curl.exe -s -o nul --max-time 2 http://127.0.0.1:%CORE_PORT%/health
    if errorlevel 1 (
        echo [..] Starting the Core on %CORE_PORT% in a new window.
        start "Clench Core :%CORE_PORT%" /d "%REPO%" cmd /k "set "ELEVENLABS_PREWARM=false" && uv run uvicorn core.main:app --port %CORE_PORT%"
    ) else (
        echo [ok] Core already running on %CORE_PORT%.
    )

    curl.exe -s -o nul --max-time 2 http://127.0.0.1:%WEB_PORT%/
    if errorlevel 1 (
        echo [..] Starting the web board on %WEB_PORT% in a new window.
        start "Clench Web :%WEB_PORT%" /d "%REPO%" cmd /k "set "CORE_URL=http://127.0.0.1:%CORE_PORT%" && npm --prefix web run dev -- --port %WEB_PORT% --strictPort --host 127.0.0.1"
    ) else (
        echo [ok] Web board already running on %WEB_PORT%.
    )
)

rem --- build and install (the servers start up meanwhile) ---------------------------------------
echo [..] Building and installing the tablet app...
call "%TABLET_DIR%gradlew.bat" -p "%TABLET_DIR%." installDebug
if errorlevel 1 (echo [x] Build or install failed, see above. & goto :fail)
echo [ok] Latest app installed.

rem --- connect and open ------------------------------------------------------------------------
if "%NEED_SERVERS%"=="0" goto :open
"%ADB%" reverse tcp:%TABLET_PORT% tcp:%WEB_PORT% >nul || goto :fail
echo [ok] Tablet localhost:%TABLET_PORT% now reaches this laptop's port %WEB_PORT%.

echo [..] Waiting for the Core and the web board...
set /a TRIES=0
:wait
curl.exe -s -o nul --max-time 2 http://127.0.0.1:%WEB_PORT%/ && curl.exe -s -o nul --max-time 2 http://127.0.0.1:%CORE_PORT%/health && goto :ready
set /a TRIES+=1
if %TRIES% geq 60 (echo [x] The servers did not come up in 60 s. Check their windows. & goto :fail)
timeout /t 1 /nobreak >nul
goto :wait
:ready
echo [ok] Servers are up.

:open
"%ADB%" shell am force-stop %PKG% >nul 2>&1
"%ADB%" shell am start -n %PKG%/%ACTIVITY% >nul || goto :fail
echo.
echo [ok] Opened %ACTIVITY% on the tablet.
if "%NEED_SERVERS%"=="1" (
    echo      Leave the Core and Web windows open while you use the board. Close them to stop.
    echo      Unplugged the tablet? Run this script again to reconnect.
)
echo.
pause
exit /b 0

:fail
echo.
pause
exit /b 1
