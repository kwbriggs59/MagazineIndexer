@echo off
echo Copying database to Pi...
scp "%~dp0dist\MagazineLibrary\database\magazine_library.db" kevin@192.168.0.30:/home/kevin/magazine_library.db
if errorlevel 1 (
    echo ERROR: SCP failed.
    pause
    exit /b 1
)
echo Done.
pause