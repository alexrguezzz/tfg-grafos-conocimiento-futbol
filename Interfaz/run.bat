@echo off
setlocal
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
  echo No se encontro Python en PATH.
  echo Activa tu entorno ^(por ejemplo: conda activate tfg^) y vuelve a ejecutar run.bat
  exit /b 1
)

python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python app.py
