@echo off
setlocal EnableDelayedExpansion

echo ================================================
echo  Magazine Library -- Build Distributable
echo ================================================
echo.

:: -------------------------------------------------------
:: Verify we're running in the right Python environment
:: -------------------------------------------------------
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python not found in PATH.
    echo Activate the conda environment first:
    echo   conda activate mag
    pause
    exit /b 1
)

for /f "tokens=*" %%i in ('python -c "import sys; print(sys.executable)"') do set PYTHON_EXE=%%i
echo Using Python: %PYTHON_EXE%
echo.

:: -------------------------------------------------------
:: Install / upgrade PyInstaller
:: -------------------------------------------------------
echo Installing PyInstaller...
pip install --quiet --upgrade pyinstaller
if errorlevel 1 (
    echo ERROR: Failed to install PyInstaller.
    pause
    exit /b 1
)

:: -------------------------------------------------------
:: Preserve database across clean
:: -------------------------------------------------------
set DB_SRC=dist\MagazineLibrary\database\magazine_library.db
set DB_TMP=%TEMP%\magazine_library_build_backup.db
if exist "%DB_SRC%" (
    echo Preserving existing database...
    copy "%DB_SRC%" "%DB_TMP%" >nul
)
echo.

:: -------------------------------------------------------
:: Clean previous build artefacts
:: -------------------------------------------------------
echo Cleaning previous build...
if exist build     rmdir /s /q build
if exist dist      rmdir /s /q dist
if exist MagazineLibrary.spec del /q MagazineLibrary.spec
echo.

:: -------------------------------------------------------
:: Build
:: -------------------------------------------------------
echo Building...
echo.

pyinstaller ^
  --onedir ^
  --windowed ^
  --name "MagazineLibrary" ^
  --add-data "woodcarving_illustrated_article_index.csv;." ^
  --collect-all fitz ^
  --collect-all sqlalchemy ^
  --collect-all anthropic ^
  --hidden-import sqlalchemy.dialects.sqlite ^
  --hidden-import PyQt6.QtPrintSupport ^
  --noconfirm ^
  main.py

if errorlevel 1 (
    echo.
    echo ERROR: Build failed -- see output above.
    pause
    exit /b 1
)

:: -------------------------------------------------------
:: Post-build: create writable folders the app expects
:: -------------------------------------------------------
echo.
echo Creating runtime folders...
mkdir "dist\MagazineLibrary\database" 2>nul

:: -------------------------------------------------------
:: Restore preserved database
:: -------------------------------------------------------
if exist "%DB_TMP%" (
    echo Restoring database...
    copy "%DB_TMP%" "%DB_SRC%" >nul
    del "%DB_TMP%"
)

:: -------------------------------------------------------
:: Done
:: -------------------------------------------------------
echo.
echo ================================================
echo  Build complete
echo ================================================
echo.
echo  Executable : dist\MagazineLibrary\MagazineLibrary.exe
echo  Distribute : zip the entire dist\MagazineLibrary\ folder
echo.
echo  IMPORTANT -- Tesseract OCR is NOT bundled.
echo  Install it separately on each machine if you
echo  plan to import image-only (non-OCR'd) PDFs:
echo    https://github.com/UB-Mannheim/tesseract/wiki
echo  Expected path: C:\Program Files\Tesseract-OCR\tesseract.exe
echo.
pause
