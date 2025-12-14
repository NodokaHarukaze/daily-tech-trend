@echo off
setlocal enabledelayedexpansion

cd /d C:\work\daily-tech-trend

REM ログフォルダ
if not exist logs mkdir logs

REM JSTで日付ファイル名（PowerShellで生成）
for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmmss"') do set TS=%%i
set LOG=logs\run_%TS%.log

echo ==== Daily Tech Trend start %date% %time% ==== > "%LOG%"

REM Python実行（失敗したら即終了してログに残す）
py -3.11 src\collect.py >> "%LOG%" 2>&1
if errorlevel 1 goto :fail

py -3.11 src\normalize.py >> "%LOG%" 2>&1
if errorlevel 1 goto :fail

py -3.11 src\normalize_categories.py >> "%LOG%" 2>&1
if errorlevel 1 goto :fail

py -3.11 src\dedupe.py >> "%LOG%" 2>&1
if errorlevel 1 goto :fail

py -3.11 src\thread.py >> "%LOG%" 2>&1
if errorlevel 1 goto :fail

py -3.11 src\translate.py >> "%LOG%" 2>&1
if errorlevel 1 goto :fail

py -3.11 src\llm_insights_local.py >> "%LOG%" 2>&1
if errorlevel 1 goto :fail

py -3.11 src\render.py >> "%LOG%" 2>&1
if errorlevel 1 goto :fail

REM 生成物に差分がある時だけ commit/push（差分なしは成功扱い）
git add docs\index.html >> "%LOG%" 2>&1
if errorlevel 1 goto :fail

git diff --cached --quiet >> "%LOG%" 2>&1
if errorlevel 1 (
  git commit -m "daily update (local LLM)" >> "%LOG%" 2>&1
  if errorlevel 1 goto :fail

  git push >> "%LOG%" 2>&1
  if errorlevel 1 goto :fail
) else (
  echo No changes: docs/index.html >> "%LOG%"
)

REM 30日以上前のログ削除
dir /b logs\run_*.log >nul 2>nul
if not errorlevel 1 (
  forfiles /p logs /m run_*.log /d -30 /c "cmd /c del @path" >> "%LOG%" 2>nul
) else (
  echo No log files to cleanup >> "%LOG%"
)

echo ==== SUCCESS %date% %time% ==== >> "%LOG%"
exit /b 0

:fail
echo ==== FAILED %date% %time% ==== >> "%LOG%"
exit /b 1
