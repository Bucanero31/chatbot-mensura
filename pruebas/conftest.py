import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))
if (RAIZ / "libs").exists():
    sys.path.insert(0, str(RAIZ / "libs"))


@pytest.fixture(scope="session")
def buscador():
    from chatbot.buscador import Buscador
    return Buscador()
