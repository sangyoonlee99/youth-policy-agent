@echo off
rem 청년정책 매칭 파이프라인 주간 실행 (Windows 작업 스케줄러에서 호출)
chcp 65001 > nul
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"
if not exist logs mkdir logs
echo ===== %date% %time% ===== >> logs\run.log
".venv\Scripts\python.exe" -m src.main >> logs\run.log 2>&1
