@echo off
rem Downloads the fairy's voice (Kokoro neural voice, about 120 MB) into .local\voice\ (never committed).
cd /d "%~dp0"
".venv\Scripts\python.exe" -m game_assistant.tools.get_data voice
pause
