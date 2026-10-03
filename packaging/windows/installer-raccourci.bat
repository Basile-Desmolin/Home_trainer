@echo off
rem Installe l'appli dans un environnement Python dédié et crée un raccourci
rem « Home trainer » avec icône sur le Bureau et dans le menu Démarrer.
rem Nécessite Python 3.10+ (python.org, cocher « Add to PATH »).
rem À lancer depuis le dossier du dépôt : double-clic sur packaging\windows\installer-raccourci.bat
cd /d "%~dp0\..\.."
set "ROOT=%CD%"
py -3 -m venv .venv || goto :erreur
.venv\Scripts\python -m pip install --upgrade pip
.venv\Scripts\python -m pip install -e ".[gui,ble,ant]" || goto :erreur
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$s = New-Object -ComObject WScript.Shell;" ^
  "foreach ($dir in @([Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('Programs'))) {" ^
  "  $l = $s.CreateShortcut((Join-Path $dir 'Home trainer.lnk'));" ^
  "  $l.TargetPath = '%ROOT%\.venv\Scripts\home-trainer-gui.exe';" ^
  "  $l.WorkingDirectory = '%ROOT%';" ^
  "  $l.IconLocation = '%ROOT%\src\home_trainer\ui\assets\icon.ico';" ^
  "  $l.Description = 'Home trainer - seances et pilotage Wahoo';" ^
  "  $l.Save() }" || goto :erreur
echo.
echo Raccourci "Home trainer" cree sur le Bureau et dans le menu Demarrer.
pause
exit /b 0
:erreur
echo Echec de l'installation.
pause
exit /b 1
