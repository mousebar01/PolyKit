@echo off
setlocal

echo  PolyKit — Production Launcher
echo ================================
echo.

:: Check Node.js
where node >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Node.js is not installed or not in PATH.
    echo         Download it from https://nodejs.org
    pause
    exit /b 1
)

:: Install dependencies if node_modules is missing
if not exist "node_modules\" (
    echo [1/2] Installing dependencies...
    call npm install
    if errorlevel 1 (
        echo [ERROR] npm install failed.
        pause
        exit /b 1
    )
    echo.
)

:: Build if the Web bundle is missing
if not exist "dist-web\" (
    echo [2/2] Building the app...
    call npm run web:build
    if errorlevel 1 (
        echo [ERROR] Build failed.
        pause
        exit /b 1
    )
    echo.
)

:: Launch
echo Launching PolyKit...
call npm run web:preview

endlocal
