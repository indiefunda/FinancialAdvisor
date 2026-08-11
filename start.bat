@echo off
REM ============================================================
REM SEC Data Downloader — One-Click Launcher (Windows)
REM ============================================================
REM
REM WHAT THIS DOES:
REM   1. Checks that Python is installed on your computer
REM   2. Installs all required Python libraries (streamlit,
REM      datamule, playwright) automatically
REM   3. Downloads the Chromium browser engine (needed for
REM      transcript scraping)
REM   4. Launches the SEC Data Downloader web app
REM
REM HOW TO USE:
REM   Just double-click this file. That's it.
REM
REM WHAT IS A .BAT FILE?
REM   A batch file is a Windows script that runs commands
REM   automatically, one after another. Think of it like a
REM   recipe that your computer follows.
REM
REM TROUBLESHOOTING:
REM   - If you see "Python is not installed", go to
REM     https://www.python.org/downloads/ and install Python.
REM     Make sure to check "Add Python to PATH" during install.
REM   - If the app doesn't open, close this window and
REM     double-click start.bat again.
REM ============================================================

REM Set the window title so you know what this is
title SEC Data Downloader

echo ============================================
echo  SEC Data Downloader - Setup ^& Launch
echo ============================================
echo.

REM ── Step 1: Check if Python exists ───────────────────────────────
REM "python --version" asks Python to print its version number.
REM ">nul 2>&1" hides the output — we only care if it succeeds or fails.
REM "%errorlevel%" is the exit code: 0 = success, anything else = error.
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python is not installed or not in PATH.
    echo Download Python from https://www.python.org/downloads/
    echo Make sure to check "Add Python to PATH" during installation.
    pause
    exit /b 1
)

REM ── Step 2: Install Python libraries ─────────────────────────────
REM "pip" is Python's package installer. It downloads libraries from
REM the internet. The "-q" flag means "quiet" (less output).
REM "%~dp0" means "the folder where this .bat file is located".
echo [1/3] Installing Python dependencies...
python -m pip install --upgrade pip -q
python -m pip install -r "%~dp0requirements.txt" -q
if %errorlevel% neq 0 (
    echo [ERROR] Failed to install dependencies.
    pause
    exit /b 1
)
echo       Done.

REM ── Step 3: Install Playwright browser engine ────────────────────
REM Playwright needs a real Chrome browser to scrape websites.
REM This command downloads a private copy of Chromium just for this app.
echo [2/3] Ensuring Playwright browser engine...
python -m playwright install chromium -q
if %errorlevel% neq 0 (
    echo [WARNING] Playwright browser install had issues. Transcripts may not work.
)

REM ── Step 4: Launch the Streamlit app ─────────────────────────────
REM "streamlit run app.py" starts the web server.
REM It will open your browser automatically at http://localhost:8501
echo [3/3] Launching app...
echo.
echo   The app will open in your browser at http://localhost:8501
echo   Close this window to stop the server.
echo.
echo ============================================
echo.

python -m streamlit run "%~dp0app.py"

REM If the app crashes, show an error and wait for user to press a key
if %errorlevel% neq 0 (
    echo.
    echo [ERROR] App crashed. Check the error messages above.
    pause
)
