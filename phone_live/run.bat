@echo off
chcp 65001 >nul
cd /d "%~dp0"
rem py 실행기가 있으면 기본 파이썬(패키지 설치된 버전)으로, 없으면 python
where py >nul 2>nul && (py server.py %*) || (python server.py %*)
pause
