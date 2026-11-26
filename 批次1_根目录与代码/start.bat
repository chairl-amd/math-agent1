@echo off
rem ============================================================
rem  math-agent portable launcher  (new file, this round)
rem
rem  What it does
rem    1) cd into this file's own folder -> no drive letter is hardcoded
rem    2) read KEY=VALUE pairs from .env next to this file
rem       (this file never stores a key)
rem    3) use .python312\python.exe if present, else python from PATH
rem    4) start the Gradio web UI on http://127.0.0.1:7860
rem
rem  Usage
rem    double-click it, or     start.bat
rem    start.bat --check       show environment status, start nothing
rem    start.bat --open        also open the browser
rem    port: put MATH_AGENT_PORT=7861 in .env
rem ============================================================
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul 2>&1

set "BASE=%~dp0"
if "%BASE:~-1%"=="\" set "BASE=%BASE:~0,-1%"
cd /d "%BASE%" 2>nul
if errorlevel 1 (
    echo [ERROR] cannot enter folder: %BASE%
    pause
    exit /b 2
)

rem ---------- 1) find a usable interpreter ----------
set "PY="
if exist "%BASE%\.python312\python.exe" set "PY=%BASE%\.python312\python.exe"
if defined PY (
    "%PY%" -c "import sys" >nul 2>&1
    if errorlevel 1 set "PY="
)
if not defined PY (
    where python >nul 2>&1
    if not errorlevel 1 (
        python -c "import sys" >nul 2>&1
        if not errorlevel 1 set "PY=python"
    )
)
if not defined PY (
    echo [ERROR] No usable Python 3.12 found.
    echo.
    echo   Option A^: keep the bundled .python312 folder next to this file.
    echo   Option B^: install Python 3.12 from python.org, tick
    echo             "Add python.exe to PATH", then run:
    echo               pip install -r requirements.txt
    echo.
    pause
    exit /b 2
)

rem ---------- 2) load .env (KEY=VALUE, '#' starts a comment) ----------
set "ENVFILE=%BASE%\.env"
if not exist "%ENVFILE%" (
    echo [WARN] .env not found.
    echo        Copy .env.example to .env, fill in your own keys, save, retry.
) else (
    for /f "usebackq eol=# tokens=1,* delims==" %%A in ("%ENVFILE%") do (
        set "_K=%%A"
        set "_V=%%B"
        if defined _V (
            for /f "tokens=* delims= " %%S in ("!_K!") do set "_K=%%S"
            for /f "tokens=* delims= " %%S in ("!_V!") do set "_V=%%S"
            set "_V=!_V:"=!"
            if not "!_K!"=="" set "!_K!=!_V!"
        )
    )
)

rem ---------- 3) defaults that are safe to pin ----------
if not defined QWEN_VERIFIER_MODEL set "QWEN_VERIFIER_MODEL=qwen3-vl-plus"
if not defined PYTHONUTF8 set "PYTHONUTF8=1"
if not defined PYTHONIOENCODING set "PYTHONIOENCODING=utf-8"

set "APP=%BASE%\web\app.py"
if defined MATH_AGENT_ROOT if exist "%MATH_AGENT_ROOT%\web\app.py" set "APP=%MATH_AGENT_ROOT%\web\app.py"
set "PORT=7860"
if defined MATH_AGENT_PORT set "PORT=%MATH_AGENT_PORT%"
set "OPEN_BROWSER=0"
set "CHECK_ONLY=0"
if /i "%~1"=="--check" set "CHECK_ONLY=1"
if /i "%~1"=="--open" set "OPEN_BROWSER=1"
if /i "%~2"=="--open" set "OPEN_BROWSER=1"

if "%CHECK_ONLY%"=="1" goto :check

rem ---------- 4) pre-flight ----------
if not exist "%APP%" (
    echo [ERROR] web\app.py not found: %APP%
    echo        Run this file from inside the math-agent folder.
    pause
    exit /b 2
)
if not defined DEEPSEEK_API_KEY (
    echo [WARN] DEEPSEEK_API_KEY is empty: the UI opens, but every check
    echo        will fail with a model-call error until you fill in .env.
    echo.
)

echo [INFO] python : %PY%
echo [INFO] app    : %APP%
echo [INFO] open    http://127.0.0.1:%PORT%
echo [INFO] press Ctrl+C or close this window to stop the server.
echo.
if "%OPEN_BROWSER%"=="1" start "" "http://127.0.0.1:%PORT%"
"%PY%" "%APP%" --host 127.0.0.1 --port %PORT%
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" (
    echo.
    echo [ERROR] web app exited with code %RC%
    pause
)
endlocal & exit /b %RC%

rem ---------- --check ----------
:check
echo [CHECK] base   = %BASE%
echo [CHECK] python = %PY%
"%PY%" -c "import sys;print('[CHECK] version =',sys.version.split()[0])" 2>nul
"%PY%" -c "import gradio,openai,sympy,PIL;print('[CHECK] deps ok')" 2>nul || echo [CHECK] deps MISSING - run: pip install -r requirements.txt
if exist "%APP%" (echo [CHECK] app exists = yes) else (echo [CHECK] app exists = NO)
if exist "%BASE%\.env" (echo [CHECK] .env exists = yes) else (echo [CHECK] .env exists = NO)
if defined DEEPSEEK_API_KEY (echo [CHECK] DEEPSEEK_API_KEY = set) else (echo [CHECK] DEEPSEEK_API_KEY = missing)
if defined DASHSCOPE_API_KEY (echo [CHECK] DASHSCOPE_API_KEY = set) else (echo [CHECK] DASHSCOPE_API_KEY = missing)
if defined QWEN_VERIFIER_MODEL (echo [CHECK] QWEN_VERIFIER_MODEL = %QWEN_VERIFIER_MODEL%) else (echo [CHECK] QWEN_VERIFIER_MODEL = missing)
echo [CHECK] port   = %PORT%
endlocal & exit /b 0
