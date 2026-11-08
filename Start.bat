@echo off
chcp 65001 > nul
cd /d "%~dp0"

REM Python 実行コマンドの検出
set PYCMD=
where python >nul 2>&1 && set PYCMD=python
if not defined PYCMD where py >nul 2>&1 && set PYCMD=py
if not defined PYCMD where python3 >nul 2>&1 && set PYCMD=python3

if not defined PYCMD (
    echo [ERROR] Python が見つかりません。Python をインストールしてください。
    pause
    exit /b 1
)

%PYCMD% start_server.py %*
if %ERRORLEVEL% neq 0 (
    echo.
    echo [ERROR] サーバー起動処理が異常終了しました (Exit Code: %ERRORLEVEL%)。
    pause
)
