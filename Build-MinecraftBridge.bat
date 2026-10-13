@echo off
rem Builds the Minecraft bridge (bridges\minecraft, a Fabric mod for Minecraft Java 26.3) and copies it into
rem Minecraft's mods folder. Needs the portable JDK 25 in .local\tools (see docs\games\minecraft.md).
cd /d "%~dp0bridges\minecraft"
for /d %%J in ("%~dp0.local\tools\jdk-25*") do set "JAVA_HOME=%%J"
if not defined JAVA_HOME (
  echo No JDK 25 in .local\tools: see docs\games\minecraft.md
  pause
  exit /b 1
)
set "GRADLE_USER_HOME=%~dp0.local\gradle"
call gradlew.bat build --no-daemon || (pause & exit /b 1)
if not exist "%APPDATA%\.minecraft\mods" mkdir "%APPDATA%\.minecraft\mods"
del /q "%APPDATA%\.minecraft\mods\gabridge-*.jar" 2>nul
copy /y build\libs\gabridge-*.jar "%APPDATA%\.minecraft\mods\" >nul && echo Installed into %APPDATA%\.minecraft\mods
pause
