@echo off
rem Construit dist\HomeTrainer.exe (aucun Python nécessaire pour le lancer ensuite).
rem À lancer depuis le dossier du dépôt : packaging\windows\construire-exe.bat
cd /d "%~dp0\..\.."
py -3 -m venv .venv-build || goto :erreur
call .venv-build\Scripts\activate.bat
python -m pip install --upgrade pip
python -m pip install ".[gui,ble,ant]" pyinstaller || goto :erreur
pyinstaller --noconfirm packaging\HomeTrainer.spec || goto :erreur
echo.
echo Termine : dist\HomeTrainer.exe
explorer dist
exit /b 0
:erreur
echo Echec de la construction.
pause
exit /b 1
