@echo off
rem Same as Assistant.bat. The assistant: chat window, fairy companion, and voice (hold F9 to talk). Default game: eldenring.
rem Elden Ring: start it with Attack on Elden Ring's Play-EldenRing.bat first. Settings: .local\settings.json
cd /d "%~dp0"
start "" ".venv\Scripts\pythonw.exe" -m game_assistant.app %*
