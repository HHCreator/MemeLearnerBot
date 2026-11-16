@echo off
rem Stops the meme bot's auto run: ends and deletes its scheduled tasks.
rem What it found so far stays: the report .bat beside this one shows it.
cd /d "%~dp0"
py -m memelearner auto stop
echo.
pause
