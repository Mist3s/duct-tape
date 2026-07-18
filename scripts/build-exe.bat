@echo off
rem Сборка программы в один файл omsreg.exe (нужен Python и интернет для PyInstaller).
chcp 65001 >nul
set "ROOT=%~dp0.."
cd /d "%ROOT%"
set PY=python
where python >nul 2>nul || set PY=py

echo ============================================================
echo   Сборка программы в один файл dist\omsreg.exe
echo ============================================================
echo.
echo [1/2] Проверка/установка PyInstaller...
%PY% -m pip install --upgrade pyinstaller
if errorlevel 1 goto err

echo.
echo [2/2] Сборка (займёт 1-2 минуты)...
%PY% -m PyInstaller --noconfirm --clean --onefile --windowed ^
  --name omsreg --paths src ^
  --icon src\omsreg\gui\assets\icon.ico ^
  --add-data "src\omsreg\gui\assets\icon.png;omsreg\gui\assets" ^
  src\omsreg\__main__.py
if errorlevel 1 goto err

echo.
if exist "dist\omsreg.exe" (
  echo ГОТОВО. Файл: dist\omsreg.exe
  echo Его можно скопировать на любой компьютер с Windows - Python там уже НЕ нужен.
) else (
  echo Похоже, сборка не создала exe - смотрите сообщения выше.
)
goto end
:err
echo.
echo *** ОШИБКА при сборке - смотрите сообщения выше. ***
:end
echo.
pause
