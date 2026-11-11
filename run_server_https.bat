@echo off
echo ====================================================
echo Starting BaymaxCare HTTPS Server (Enables Mic on Phone)
echo ====================================================
call .venv\Scripts\activate.bat
python -m uvicorn server:app --host 0.0.0.0 --port 8000 --ssl-keyfile key.pem --ssl-certfile cert.pem --reload
pause

