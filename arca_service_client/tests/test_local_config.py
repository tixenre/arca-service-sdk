"""Tests de local_config -- guardado/carga de perfiles (`arca-service-client login`,
ver cli.py). `isolated_config_dir` (conftest.py) asegura que ningún test toque el
`~/.config/arca-service` real de quien corre la suite."""

from __future__ import annotations

import stat
import sys

import pytest

from arca_service_client.local_config import (
    CredentialsNotFoundError,
    Profile,
    config_dir,
    load_profile,
    save_profile,
)


def _profile(**overrides):
    kwargs = dict(
        base_url="https://arca.test",
        api_key="arca_test-key",
        client_cert_path="",
        client_key_path="",
        plataforma_slug="acme",
    )
    kwargs.update(overrides)
    return Profile(**kwargs)


def test_save_y_load_profile_ida_y_vuelta(isolated_config_dir, client_cert_pem):
    cert_pem, key_pem = client_cert_pem

    save_profile("default", _profile(), cert_pem=cert_pem, key_pem=key_pem)
    loaded = load_profile("default")

    assert loaded.base_url == "https://arca.test"
    assert loaded.api_key == "arca_test-key"
    assert loaded.plataforma_slug == "acme"
    assert loaded.client_cert_path.endswith("default.crt")
    assert loaded.client_key_path.endswith("default.key")


def test_save_profile_escribe_el_pem_real_en_disco(isolated_config_dir, client_cert_pem):
    cert_pem, key_pem = client_cert_pem

    save_profile("default", _profile(), cert_pem=cert_pem, key_pem=key_pem)
    loaded = load_profile("default")

    with open(loaded.client_cert_path) as f:
        assert f.read() == cert_pem
    with open(loaded.client_key_path) as f:
        assert f.read() == key_pem


@pytest.mark.skipif(sys.platform == "win32", reason="permisos POSIX")
def test_save_profile_deja_los_archivos_sensibles_con_permisos_restrictivos(
    isolated_config_dir, client_cert_pem
):
    cert_pem, key_pem = client_cert_pem

    save_profile("default", _profile(), cert_pem=cert_pem, key_pem=key_pem)
    loaded = load_profile("default")

    assert stat.S_IMODE(config_dir().stat().st_mode) == 0o700
    assert stat.S_IMODE(open(loaded.client_cert_path, "rb").fileno().__index__() and 0 or 0) == 0
    import os

    assert stat.S_IMODE(os.stat(loaded.client_cert_path).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(loaded.client_key_path).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(config_dir() / "credentials.json").st_mode) == 0o600


def test_dos_perfiles_conviven_sin_pisarse(isolated_config_dir, client_cert_pem):
    cert_pem, key_pem = client_cert_pem

    save_profile("acme", _profile(plataforma_slug="acme"), cert_pem=cert_pem, key_pem=key_pem)
    save_profile("beta", _profile(plataforma_slug="beta"), cert_pem=cert_pem, key_pem=key_pem)

    assert load_profile("acme").plataforma_slug == "acme"
    assert load_profile("beta").plataforma_slug == "beta"


def test_load_profile_inexistente_da_credentials_not_found_error(isolated_config_dir):
    with pytest.raises(CredentialsNotFoundError, match="login"):
        load_profile("no-existe")


def test_config_dir_respeta_xdg_config_home(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert config_dir() == tmp_path / "arca-service"
