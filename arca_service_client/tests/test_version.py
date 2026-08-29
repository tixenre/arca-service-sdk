"""`pyproject.toml` se sincroniza A MANO con `__init__.__version__` (no hay build
tooling que lo haga automático) — este test evita que se desincronicen sin que nadie
lo note."""

from __future__ import annotations

import re
from pathlib import Path

import arca_service_client

_PYPROJECT = Path(__file__).resolve().parent.parent / "pyproject.toml"


def test_version_semver():
    parts = arca_service_client.__version__.split(".")
    assert len(parts) == 3, f"__version__ no es SemVer: {arca_service_client.__version__!r}"
    assert all(p.isdigit() for p in parts), f"__version__ no es numérico: {parts!r}"


def test_pyproject_version_coincide_con_init():
    contenido = _PYPROJECT.read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', contenido, re.MULTILINE)
    assert m is not None, 'pyproject.toml no tiene una línea `version = "X.Y.Z"`'
    assert m.group(1) == arca_service_client.__version__, (
        f"pyproject.toml dice version={m.group(1)!r} pero "
        f"arca_service_client.__version__={arca_service_client.__version__!r} — desincronizados"
    )


def test_all_exports_son_importables():
    for name in arca_service_client.__all__:
        assert hasattr(arca_service_client, name), f"'{name}' en __all__ pero no importable"
