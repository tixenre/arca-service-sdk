"""Tests de crypto.seal() — sin red. Verifica el roundtrip completo contra una
implementación de referencia del descifrado (RSA-OAEP + AES-256-GCM), no solo el shape
del dict: si `seal()` alguna vez se desincroniza del esquema que espera el servidor
(ej. un padding distinto), esto lo detecta acá en vez de recién al pegarle a
arca-service real."""

from __future__ import annotations

import base64

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from arca_service_client.crypto import EnvelopeError, seal

_OAEP_PADDING = padding.OAEP(
    mgf=padding.MGF1(algorithm=hashes.SHA256()), algorithm=hashes.SHA256(), label=None
)


def _keypair():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    priv_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    pub_pem = key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM, format=serialization.PublicFormat.SubjectPublicKeyInfo
    )
    return priv_pem, pub_pem


def _unseal(sealed: dict, private_key_pem: bytes) -> bytes:
    """Réplica mínima del descifrado que hace el servidor — solo para verificar el
    roundtrip en el test, no vive en el paquete (descifrar es responsabilidad de
    arca-service, nunca de este cliente)."""
    private_key = serialization.load_pem_private_key(private_key_pem, password=None)
    aes_key = private_key.decrypt(base64.b64decode(sealed["ek"]), _OAEP_PADDING)
    nonce = base64.b64decode(sealed["n"])
    ciphertext = base64.b64decode(sealed["ct"])
    return AESGCM(aes_key).decrypt(nonce, ciphertext, None)


def test_seal_roundtrip():
    priv_pem, pub_pem = _keypair()
    plaintext = b'{"key_pem": "-----BEGIN PRIVATE KEY-----...", "key_password": null}'

    sealed = seal(plaintext, pub_pem)

    assert sealed["v"] == "1"
    assert set(sealed.keys()) == {"v", "ek", "n", "ct"}
    assert _unseal(sealed, priv_pem) == plaintext


def test_seal_produce_valores_distintos_cada_vez():
    """Nonce/clave AES efímeros por llamada — dos sellados del MISMO plaintext no deben
    producir el mismo ciphertext (si lo hicieran, el nonce se estaría reusando, lo que
    rompe la seguridad de AES-GCM)."""
    _, pub_pem = _keypair()
    a = seal(b"mismo contenido", pub_pem)
    b = seal(b"mismo contenido", pub_pem)
    assert a["ct"] != b["ct"]
    assert a["n"] != b["n"]


def test_seal_clave_publica_invalida_levanta_envelope_error():
    with pytest.raises(EnvelopeError, match="PEM válida"):
        seal(b"data", b"no es una clave PEM")


def test_seal_clave_privada_pasada_por_error_levanta_envelope_error():
    """Bug real de integración: pasar la clave PRIVADA donde iba la pública — `seal()`
    tiene que rechazarlo con un mensaje claro, no fallar con un error críptico de
    `cryptography` o (peor) cifrar contra la clave equivocada en silencio.
    `load_pem_public_key` ni siquiera reconoce el header PKCS8 de una privada como
    forma de clave pública — cae en el mismo branch que "PEM inválida", confirmado
    corriendo esto contra `cryptography` real antes de escribir el test (no asumido)."""
    priv_pem, _ = _keypair()
    with pytest.raises(EnvelopeError, match="PEM válida"):
        seal(b"data", priv_pem)


def test_seal_clave_publica_no_rsa_levanta_envelope_error():
    """`seal()` es RSA-OAEP específicamente — una clave pública válida pero de otro
    algoritmo (EC) tiene que rechazarse explícitamente, no fallar más adelante contra
    `public_key.encrypt()` con un `AttributeError` críptico (EC keys no tienen
    `.encrypt()`)."""
    from cryptography.hazmat.primitives.asymmetric import ec

    ec_key = ec.generate_private_key(ec.SECP256R1())
    ec_pub_pem = ec_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM, format=serialization.PublicFormat.SubjectPublicKeyInfo
    )
    with pytest.raises(EnvelopeError, match="no es RSA"):
        seal(b"data", ec_pub_pem)
