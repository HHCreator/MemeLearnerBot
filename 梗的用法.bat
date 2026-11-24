@echo off
rem The model writes when and how each learned meme is said (the memes that don't have
rem it yet), then the page opens: change what's wrong, or tick the box when it's right.
rem Keep this window open until you click the finish button on the page.
cd /d "%~dp0"
py -m memelearner uses %*
echo.
pause
