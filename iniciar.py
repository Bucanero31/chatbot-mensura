"""Arranca el Chatbot Mensura desde cualquier carpeta: `python iniciar.py [--puerto 8013]`.

Pone la raíz del proyecto y `libs/` en la ruta de Python, así funciona igual desde
INICIAR.bat, desde el portal de la plataforma o lanzado a mano desde otro directorio.
"""

import os
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
sys.path[:0] = [str(RAIZ), str(RAIZ / "libs")]
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.chdir(RAIZ)

from chatbot.servidor import main  # noqa: E402

sys.exit(main())
