@echo off
rem Measures Claude Haiku 5.5's speed from this PC (about 20 tiny calls, under one cent).
rem Needs ANTHROPIC_API_KEY (setx ANTHROPIC_API_KEY "sk-ant-...", then a new window).
cd /d "%~dp0"
".venv\Scripts\python.exe" -m game_assistant.tools.haiku_latency %*
pause
