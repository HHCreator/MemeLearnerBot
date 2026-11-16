@echo off
rem Opens what the auto run found as the report in the browser: mark, decide, write your
rem own meanings, then click the finish button on the page. Keep this window open until then.
cd /d "%~dp0"
py -m memelearner auto report %*
echo.
pause
