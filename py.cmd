@echo off
setlocal

if defined SLIVIN_HARNESS_PYTHON (
  "%SLIVIN_HARNESS_PYTHON%" %*
  exit /b %ERRORLEVEL%
)

where py >nul 2>nul
if %ERRORLEVEL%==0 (
  py -3 %*
  exit /b %ERRORLEVEL%
)

where python >nul 2>nul
if %ERRORLEVEL%==0 (
  python %*
  exit /b %ERRORLEVEL%
)

echo Slivin Harness requires Python 3.11+. Set SLIVIN_HARNESS_PYTHON or put Python on PATH. 1>&2
exit /b 127
