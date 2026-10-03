"""Point d'entrée de l'exécutable autonome (PyInstaller) : ouvre l'interface graphique."""

import sys

from home_trainer.ui.app import main

if __name__ == "__main__":
    sys.exit(main())
