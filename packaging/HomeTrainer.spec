# Recette PyInstaller : un seul HomeTrainer.exe, sans console, avec l'icône.
#     pip install ".[gui,ble,ant]" pyinstaller
#     pyinstaller packaging/HomeTrainer.spec
# Résultat : dist/HomeTrainer.exe (dist/HomeTrainer sous Linux / macOS).
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

root = Path(SPECPATH).parent
assets = root / "src" / "home_trainer" / "ui" / "assets"

# Pilotes chargés à la demande (Bluetooth, ANT+) : à embarquer explicitement.
hidden = collect_submodules("home_trainer")
for package in ("bleak", "openant", "usb", "winrt", "garmin_fit_sdk"):
    try:
        hidden += collect_submodules(package)
    except Exception:  # paquet absent sur cette plateforme
        pass

a = Analysis(
    [str(root / "packaging" / "launcher.py")],
    pathex=[str(root / "src")],
    datas=[(str(assets), "home_trainer/ui/assets")],
    hiddenimports=hidden,
    excludes=["tkinter"],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    name="HomeTrainer",
    icon=str(assets / "icon.ico"),
    console=False,
    upx=False,
)
