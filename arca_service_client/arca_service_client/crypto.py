"""arca_service_client.crypto — sellado de payloads sensibles para
`POST /clientes/{external_ref}/credencial/importar`.

Puerto EXACTO de `seal()` de `saas_core.envelope` (tixenre/saas-core) — SOLO esa función:
`unseal()` no hace falta acá (eso lo corre arca-service del lado de adentro, con su
clave privada, que este paquete nunca ve). No se importa `saas_core` como dependencia
a propósito — arrastraría Django y el resto del paquete, para una sola función pura de
20 líneas que no necesita nada de eso.

Por qué hace falta ESTO además de mTLS/TLS: la clave privada AFIP del contribuyente es
el dato más sensible que este flujo mueve — sellarla en el body (cifrado híbrido
RSA-OAEP + AES-256-GCM contra la clave pública que publica `GET /envelope/clave-publica`)
significa que ese texto nunca existe en claro fuera de los dos extremos reales, ni
siquiera si un proxy intermedio o una herramienta de observabilidad llegara a loguear
el body de la request. Mismo patrón que PGP/S-MIME/"envelope encryption" de cualquier
KMS de cloud — no es un capricho, es defensa en profundidad sobre TLS."""

from __future__ import annotations

import base64
import os

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_AES_KEY_SIZE = 32  # AES-256
_NONCE_SIZE = 12  # tamaño estándar de nonce para AES-GCM
_OAEP_PADDING = padding.OAEP(
    mgf=padding.MGF1(algorithm=hashes.SHA256()),
    algorithm=hashes.SHA256(),
    label=None,
)


class EnvelopeError(RuntimeError):
    """La clave pública recibida de arca-service no es válida — PEM mal formada, o no es
    RSA. Nunca una excepción cruda de `cryptography` (mismo criterio que el original)."""


def seal(plaintext: bytes, recipient_public_key_pem: bytes) -> dict[str, str]:
    """Cifra `plaintext` para que SOLO arca-service (dueño de la privada correspondiente
    a `recipient_public_key_pem`, obtenida de `GET /envelope/clave-publica`) lo pueda
    leer. Devuelve un dict de strings base64 — mismo shape que
    `lib/arca_service_phx_web/schemas/sealed_payload.ex` de arca-service (`v`/`ek`/`n`/
    `ct`), directamente JSON-serializable para el campo `sealed` de
    `ImportarCredencialIn`."""
    try:
        public_key = serialization.load_pem_public_key(recipient_public_key_pem)
    except Exception as exc:
        raise EnvelopeError("La clave pública no es una clave PEM válida.") from exc
    if not isinstance(public_key, rsa.RSAPublicKey):
        raise EnvelopeError("La clave pública no es RSA.")

    aes_key = AESGCM.generate_key(bit_length=_AES_KEY_SIZE * 8)
    nonce = os.urandom(_NONCE_SIZE)
    ciphertext = AESGCM(aes_key).encrypt(nonce, plaintext, None)
    encrypted_key = public_key.encrypt(aes_key, _OAEP_PADDING)

    return {
        "v": "1",
        "ek": base64.b64encode(encrypted_key).decode(),
        "n": base64.b64encode(nonce).decode(),
        "ct": base64.b64encode(ciphertext).decode(),
    }
