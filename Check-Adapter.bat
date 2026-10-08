@echo off
rem Live adapter tests: the gate before the assistant may use a game. Default game: eldenring.
rem Elden Ring: start it with Attack on Elden Ring's Play-EldenRing.bat and load a character first.
rem   Check-Adapter.bat            run the tests
rem   Check-Adapter.bat --look     live readout of what the look-at resolver sees
cd /d "%~dp0"
".venv\Scripts\python.exe" -m game_assistant.tools.adapter_check %*
pause
