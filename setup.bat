@echo off
python -m venv .venv-my-interception
call .venv-my-interception\Scripts\activate.bat
python -m pip install -r requirements.txt
