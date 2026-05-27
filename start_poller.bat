@echo off
cd /d C:\Users\vy.nguyenth\gmail-classifer
start /min "" .venv\Scripts\python.exe -m streamlit run streamlit_app.py --server.headless true
