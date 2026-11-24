@echo off
REM ============================================================
REM SEC & Transcripts Data Downloader — One-Click Launcher (Windows)
REM ============================================================
REM   1. Checks that Python 3.10+ is installed
REM   2. Creates/reuses an isolated .venv (keeps global Python clean)
REM   3. Installs/updates dependencies from requirements.txt
REM   4. Launches the Streamlit app at http://localhost:8501
REM ============================================================

title FinancialAdvisor - SEC & Transcripts Downloader

echo ============================================
echo  FinancialAdvisor - Setup ^& Launch
echo ============================================
echo.

python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python is not installed or not in PATH.
    echo Download Python from https://www.python.org/downloads/
    echo Make sure to check "Add Python to PATH" during installation.
    pause
    exit /b 1
)

set "VENV_DIR=%~dp0.venv"
if not exist "%VENV_DIR%\Scripts\python.exe" (
    echo [1/3] Creating local virtual environment ^(.venv^)...
    python -m venv "%VENV_DIR%"
    if %errorlevel% neq 0 (
        echo [ERROR] Failed to create virtual environment.
        pause
        exit /b 1
    )
) else (
    echo [1/3] Using existing virtual environment ^(.venv^)...
)

echo [2/3] Ensuring Python dependencies...
"%VENV_DIR%\Scripts\python.exe" -m pip install --upgrade pip -q
"%VENV_DIR%\Scripts\python.exe" -m pip install -r "%~dp0requirements.txt" -q
if %errorlevel% neq 0 (
    echo [ERROR] Failed to install dependencies.
    pause
    exit /b 1
)
echo       Done.

echo [3/3] Launching Streamlit app...
echo.
echo   App URL: http://localhost:8501
echo   Close this window to stop the server.
echo.
echo ============================================
echo.

"%VENV_DIR%\Scripts\python.exe" -m streamlit run "%~dp0app.py"

if %errorlevel% neq 0 (
    echo.
    echo [ERROR] App exited with an error. Check messages above.
    pause
)
