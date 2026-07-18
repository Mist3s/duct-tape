@echo off
rem Запуск графической программы из исходников (без установки пакета).
chcp 65001 >nul
set "ROOT=%~dp0.."
set "PYTHONPATH=%ROOT%\src;%PYTHONPATH%"
cd /d "%ROOT%"
where pythonw >nul 2>nul && ( start "" pythonw -m omsreg & exit /b 0 )
where python  >nul 2>nul && ( start "" python  -m omsreg & exit /b 0 )
echo Python не найден. Установите Python с сайта python.org
echo и при установке отметьте галочку "Add python.exe to PATH".
pause
