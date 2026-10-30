@echo off
chcp 65001 >nul
title Tool San Boss NroChibi
cd /d D:\code\nro-boss-tool

echo ============================================
echo   TOOL SAN BOSS NroChibi
echo ============================================
echo.
echo   F9   bat/tat tool
echo   F11  tam dung
echo   F12  dung han
echo.
echo   Nut tr tren man hinh game cung bat/tat duoc.
echo   Cua so dieu khien se hien, bam nut BAT de chay.
echo.

python boss_hunter.py --zones 0-25

echo.
echo Tool da dung.
pause
