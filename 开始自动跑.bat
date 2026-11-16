@echo off
rem Starts the meme bot's auto run: Windows scheduled tasks that run it for 3 days
rem (README.md, "Running by itself for days"). Options can follow, as in
rem   py -m memelearner auto start --at 09:30 --rooms 6 21144080:valorant --pull
rem (py -m memelearner auto --help lists them). The stop .bat beside this one stops it.
cd /d "%~dp0"
py -m memelearner auto start %*
echo.
pause
