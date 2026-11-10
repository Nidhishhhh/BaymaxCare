@echo off
echo ====================================================
echo Starting BaymaxCare FastAPI Server (Aiven PostgreSQL)
echo ====================================================
call .venv\Scripts\activate.bat
python -m uvicorn server:app --host 0.0.0.0 --port 8000 --reload
pause

