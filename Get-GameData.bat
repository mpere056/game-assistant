@echo off
rem Downloads the community name lists the assistant needs into .local\ (never committed).
rem Elden Ring: Paramdex name lists (enemies, items and where they are, graces, shops), about 1.4 MB.
rem "Get-GameData.bat navmesh": the game's own walkable-area meshes from your installed game, for
rem walking routes (about 20 minutes once, 230 MB).
cd /d "%~dp0"
".venv\Scripts\python.exe" -m game_assistant.tools.get_data %*
pause
