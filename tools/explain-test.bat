@echo off
rem explain-test.bat, second version (thread "Add a meme video summary bot", 2026-11-11)
rem
rem Tries the bot's explain step on memes it has learned: finds the videos and web pages
rem that explain them, reads them (and hears the videos, if you say yes to Whisper), and
rem has the model explain each meme with and without them.
rem It reads Bilibili and the web as a visitor: no login, no captcha solved.
rem It writes only to .cache\explain-test (delete that folder to undo everything);
rem the bot's own files and its working copy stay as they are.
setlocal
set CACHE=%~dp0
if not exist "%CACHE%surf\live" set CACHE=G:\MemeLearnerBot\memelearner\.cache\
if not exist "%CACHE%surf" (
  echo Can't find the bot's cache at %CACHE%surf
  goto fail
)
set CODE=%TEMP%\mlb-explain-test
set TEST=%CACHE%explain-test
set MEMELEARNER_HOME=%TEST%
echo Getting the test's code (a fresh copy in %CODE%) ...
if exist "%CODE%" rmdir /s /q "%CODE%"
git clone -q -b claude/project-thread-id2prd https://github.com/HHCreator/MemeLearnerBot "%CODE%" || goto fail
echo Copying what the bot has read to %TEST% ...
if not exist "%TEST%\surf\explainers" mkdir "%TEST%\surf\explainers"
xcopy /d /e /i /q /y "%CACHE%surf\bilibili" "%TEST%\surf\bilibili" >nul
xcopy /d /e /i /q /y "%CACHE%surf\live" "%TEST%\surf\live" >nul
copy /y "%CACHE%surf\videos.json" "%TEST%\surf\videos.json" >nul
rem The explainer videos the bot and the lookup test read already: not asked for again
if exist "%CACHE%surf\explainers\*.json" xcopy /d /q /y "%CACHE%surf\explainers\*.json" "%TEST%\surf\explainers" >nul
if exist "%CACHE%lookup-test\surf\explainers\*.json" xcopy /d /q /y "%CACHE%lookup-test\surf\explainers\*.json" "%TEST%\surf\explainers" >nul

rem Whisper, to hear the videos: the one Python has, or the one in the test's own folder
set PY=py
set VPY=%TEST%\venv\Scripts\python.exe
if not defined OMP_NUM_THREADS if %NUMBER_OF_PROCESSORS% GEQ 8 set OMP_NUM_THREADS=8
py -c "import faster_whisper" >nul 2>nul && goto run
if exist "%VPY%" "%VPY%" -c "import faster_whisper" >nul 2>nul && goto venv
echo.
echo The explainer videos have no subtitles. To know what is said in them, the test can
echo hear them with Whisper, on the processor. That needs a download of about 2 GB, once,
echo into %TEST% only.
echo Without it the test still runs: it then only reads the videos' titles, comments
echo and danmu, and the web pages.
choice /c YN /m "Download Whisper and hear the videos"
if errorlevel 2 goto run
echo Installing Whisper, a few minutes. What it prints goes to whisper-install.txt ...
if not exist "%VPY%" py -m venv "%TEST%\venv" > "%TEST%\whisper-install.txt" 2>&1
if not exist "%VPY%" goto nohear
"%VPY%" -m pip install --disable-pip-version-check faster-whisper >> "%TEST%\whisper-install.txt" 2>&1 || goto nohear
:venv
set PY="%VPY%"
goto run
:nohear
echo Whisper could not be installed: see %TEST%\whisper-install.txt
echo Going on without hearing the videos.
:run
set PYTHONPATH=%CODE%
cd /d "%TEST%" || goto fail
%PY% "%CODE%\tools\explain_test.py" %* || goto fail
echo.
echo Done. Tell Claude in the thread that it finished.
echo The results are in %TEST%\result.md
pause
goto :eof
:fail
echo.
echo It stopped with an error, shown above. Copy it to Claude in the thread.
pause
exit /b 1
