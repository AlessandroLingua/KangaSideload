@echo off
rem Avvia Kanga Sideload con un doppio clic, senza finestra del terminale.
rem Il registro tecnico finisce in %LOCALAPPDATA%\Kanga\kanga.log
setlocal
cd /d "%~dp0"
set "PYW=%~dp0.venv\Scripts\pythonw.exe"
if not exist "%PYW%" (
  echo Ambiente Python non trovato in "%~dp0.venv".
  echo Crealo una volta da questa cartella con:
  echo   uv venv --python 3.12 .venv
  echo   uv pip install --python .venv\Scripts\python.exe -e ".[dev]"
  echo.
  pause
  exit /b 1
)
start "" "%PYW%" -m kanga
