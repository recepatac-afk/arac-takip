@echo off
echo Starting Vehicle Tracking App...
start "Backend Server (8070)" python backend.py
timeout /t 2 >nul
start "Frontend Server (7071)" python -m http.server 7071
echo.
echo Application is running!
echo Access the Dashboard at: http://localhost:7071
echo API is running at: http://localhost:8070
echo.
pause
