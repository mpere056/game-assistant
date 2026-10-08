@echo off
rem Offline tests of the game-independent core (no game or API key needed).
cd /d "%~dp0"
".venv\Scripts\python.exe" -m unittest discover -s tests -t .
pause
