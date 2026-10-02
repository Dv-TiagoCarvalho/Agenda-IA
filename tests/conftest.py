"""Prepara os testes: banco separado, apagado ao final, e a API importável."""

import os
import sys
import tempfile
from pathlib import Path

# O api.py fica na raiz do projeto.
sys.path.insert(0, str(Path(__file__).parent.parent))

# Os testes usam um banco próprio, para não mexer no agenda.db de verdade.
_pasta = tempfile.TemporaryDirectory()
os.environ["DATABASE_URL"] = f"sqlite:///{Path(_pasta.name) / 'testes.db'}"
