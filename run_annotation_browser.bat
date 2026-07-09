@echo off
rem Double-click this from Explorer to launch the annotation browser.
setlocal
cd /d "%~dp0"

set "PY=%~dp0.venv\Scripts\python.exe"
if not exist "%PY%" (
  echo Project venv not found at "%PY%".
  echo Create it and install deps first, e.g.:
  echo     python -m venv .venv
  echo     .venv\Scripts\python -m pip install -r requirements.txt
  echo.
  pause
  exit /b 1
)

"%PY%" "%~dp0run_annotation_browser.py" %*
rem The Python script prints its own traceback and pauses on crash.
endlocal
