@echo off
rem Say memes: type what is going on in a match on the page that opens, and the bot
rem answers with a few danmu lines made of the memes it learned. Rate each line: funny or
rem wrong. Keep this window open until you click the finish button on the page.
cd /d "%~dp0"
py -m memelearner say %*
echo.
pause
