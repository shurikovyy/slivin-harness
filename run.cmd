@echo off
setlocal
set "ROOT=%~dp0"
if not defined SLIVIN_HARNESS_PYTHON set "SLIVIN_HARNESS_PYTHON=%USERPROFILE%\Documents\sa_icover\.venv\Scripts\python.exe"
"%SLIVIN_HARNESS_PYTHON%" "%ROOT%task_runner.py" %*
