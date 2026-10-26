@echo off
rem 打包.bat —— 一键重新打包 exe（默认两种形态：文件夹形式 + 单文件）。
rem 只想要启动快的文件夹形式：打包.bat --onedir
rem 只想要单文件：            打包.bat --onefile
rem 跳过打包后自查：          打包.bat --no-verify
chcp 65001 >nul
cd /d "%~dp0"
if exist "%~dp0.venv\Scripts\python.exe" (
  "%~dp0.venv\Scripts\python.exe" build.py %*
) else (
  python build.py %*
)
pause
