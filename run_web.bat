@echo off
chcp 65001 > nul
cd /d "%~dp0"
echo ========================================================
echo   Podcast AI Summarizer Web Dashboard
echo ========================================================
echo Webアプリを起動しています... ブラウザが自動的に開きます。
echo 終了するにはこのウィンドウで Ctrl + C を押してください。
echo.

python -m streamlit run app.py
pause
