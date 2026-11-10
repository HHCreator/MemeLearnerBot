@echo off
rem How the meme bot's auto run is going: its tasks, each run's summary line, the memes
rem found so far and the end of the log (memelearner\.cache\surf\auto\log.txt).
cd /d "%~dp0"
py -m memelearner auto status %*
echo.
pause
