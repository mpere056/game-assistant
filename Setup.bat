@echo off
rem Creates the project-local Python environment (.venv) and installs the Anthropic SDK.
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" python -m venv .venv
".venv\Scripts\python.exe" -m pip install -q -r requirements.txt
".venv\Scripts\python.exe" -m game_assistant.tools.get_data eldenring
".venv\Scripts\python.exe" -m game_assistant.tools.get_data voice
echo Done. Set your API key once with:  setx ANTHROPIC_API_KEY "sk-ant-..."   (then open a new window)
pause
