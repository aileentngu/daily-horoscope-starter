@echo off

chcp 65001 >nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8

cd /d "C:\Users\ailee\Documents\daily-horoscope-starter"

echo. >> horoscope_log.txt
echo ======================================== >> horoscope_log.txt
echo Started: %date% %time% >> horoscope_log.txt

".venv\Scripts\python.exe" horoscope.py >> horoscope_log.txt 2>&1

echo Exit code: %errorlevel% >> horoscope_log.txt
echo Finished: %date% %time% >> horoscope_log.txt