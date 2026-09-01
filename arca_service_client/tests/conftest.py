"""Fixtures compartidas — certificado mTLS autofirmado y descartable, generado en
`tmp_path`. `httpx.Client(cert=(cert_path, key_path))` carga esos archivos al construir
el cliente (antes de mandar ningún request), incluso con `pytest_httpx` mockeando el
transporte — así que hacen falta archivos REALES en disco, no rutas inventadas."""

from __future__ import annotations

import datetime

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID


def _self_signed_cert_and_key_pem() -> tuple[bytes, bytes]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name(
        [x509.NameAttribute(NameOID.COMMON_NAME, "arca-service-client-tests")]
    )
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.datetime.now(datetime.timezone.utc))
        .not_valid_after(datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=1))
        .sign(key, hashes.SHA256())
    )

    cert_pem = cert.public_bytes(serialization.Encoding.PEM)
    key_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return cert_pem, key_pem


@pytest.fixture
def client_cert_files(tmp_path):
    cert_pem, key_pem = _self_signed_cert_and_key_pem()
    cert_path = tmp_path / "client.crt"
    key_path = tmp_path / "client.key"
    cert_path.write_bytes(cert_pem)
    key_path.write_bytes(key_pem)
    return str(cert_path), str(key_path)


@pytest.fixture
def client_cert_files_mismatched(tmp_path):
    """Certificado de un par, clave de OTRO -- ambos individualmente válidos, pero no se
    corresponden. Simula lo que `CredentialsInvalidError` existe para atajar: un
    certificado/clave corrompidos o mezclados al copiarlos a mano."""
    cert_pem, _ = _self_signed_cert_and_key_pem()
    _, key_pem = _self_signed_cert_and_key_pem()
    cert_path = tmp_path / "client.crt"
    key_path = tmp_path / "client.key"
    cert_path.write_bytes(cert_pem)
    key_path.write_bytes(key_pem)
    return str(cert_path), str(key_path)


@pytest.fixture
def client_cert_pem():
    """Igual que `client_cert_files`, pero el PEM en sí (str) -- para lo que simula la
    RESPUESTA de `POST /signup` (`local_config.save_profile`/`cli.py`), que llega como
    texto, no como ruta de archivo."""
    cert_pem, key_pem = _self_signed_cert_and_key_pem()
    return cert_pem.decode(), key_pem.decode()


@pytest.fixture
def isolated_config_dir(monkeypatch, tmp_path):
    """Redirige `local_config.config_dir()` a un `tmp_path` propio de cada test --
    ningún test de `login`/perfiles debe tocar el `~/.config/arca-service` real de
    quien corre la suite."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    return tmp_path / "arca-service"
