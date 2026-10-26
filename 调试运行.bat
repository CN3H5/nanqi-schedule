@echo off
rem 调试运行.bat —— 从源码启动南七日程，控制台可见全部报错与打印。
rem 打包后的 exe 是 windowed 程序（没有控制台），调试时请用这个。
rem
rem chcp 65001：把控制台切成 UTF-8，否则打印「⋯」等字符会
rem UnicodeEncodeError（GBK 控制台的老坑）。
chcp 65001 >nul
cd /d "%~dp0"
if exist "%~dp0.venv\Scripts\python.exe" (
  "%~dp0.venv\Scripts\python.exe" main.py %*
) else (
  python main.py %*
)
pause
