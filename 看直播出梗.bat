@echo off
rem Says memes by itself while a Bilibili live room is on: it listens to the room's danmu
rem and every 30 seconds says a few lines that fit what is going on in the match. The
rem translator's overlay window shows them, to copy. It only reads: no login, nothing is sent.
rem Give the room after this file's name, or type it when asked. Ctrl+C stops it.
cd /d "%~dp0"
if not "%~1"=="" goto given
set /p "ROOM=Bilibili live room, its link or number (like 6): "
py -m memelearner live-say %ROOM%
goto done
:given
py -m memelearner live-say %*
:done
echo.
pause
