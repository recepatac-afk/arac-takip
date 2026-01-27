@echo off
echo ===================================================
echo Eski calisan python serverlari kapatiliyor...
taskkill /IM python.exe /F >nul 2>&1
echo ===================================================
echo.
echo YENI SISTEM BASLATILIYOR (V2.1)...
echo.
echo 1. Veritabani (Backend) baslatiliyor (Port 8070)...
start "BACKEND - VERITABANI - KAPATMAYIN" python backend.py
echo.
echo 2. Ekran (Frontend) baslatiliyor (Port 8000)...
start "FRONTEND - EKRAN - KAPATMAYIN" python -m http.server 8000 --bind 127.0.0.1
echo.
echo Lutfen bekleyin, tarayici aciliyor...
timeout /t 3 >nul
start http://127.0.0.1:8000
echo.
echo KURULUM TAMAMLANDI!
echo.
echo ACILAN SIYAH PENCERELERI KAPATMAYIN.
echo.
pause
