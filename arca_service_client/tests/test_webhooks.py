"""Tests de verify_webhook_signature — sin red. Firma armada con el MISMO esquema que
usa el servidor real para firmar sus webhooks (no reimplementado distinto acá por las
dudas — si el esquema real cambia, este test no lo detectaría, pero al menos confirma
que la implementación ES ese esquema)."""

from __future__ import annotations

import hashlib
import hmac
import time

from arca_service_client.webhooks import verify_webhook_signature

_SECRET = "el-webhook-secret-del-client"


def _firmar(payload: bytes, timestamp: str, secret: str = _SECRET) -> str:
    signed_payload = timestamp.encode() + b"." + payload
    return hmac.new(secret.encode(), signed_payload, hashlib.sha256).hexdigest()


def test_firma_valida_devuelve_true():
    payload = b'{"idempotency_key": "factura-1", "estado": "issued"}'
    timestamp = str(int(time.time()))
    signature = _firmar(payload, timestamp)

    assert verify_webhook_signature(payload, signature, timestamp, _SECRET) is True


def test_firma_incorrecta_devuelve_false():
    payload = b'{"estado": "issued"}'
    timestamp = str(int(time.time()))

    assert verify_webhook_signature(payload, "0" * 64, timestamp, _SECRET) is False


def test_secret_equivocado_devuelve_false():
    payload = b'{"estado": "issued"}'
    timestamp = str(int(time.time()))
    signature = _firmar(payload, timestamp, secret="otro-secret")

    assert verify_webhook_signature(payload, signature, timestamp, _SECRET) is False


def test_payload_alterado_devuelve_false():
    """El timestamp entra DENTRO del material firmado junto al payload — alterar
    cualquiera de los dos invalida la firma, no solo alterar el timestamp."""
    timestamp = str(int(time.time()))
    signature = _firmar(b'{"estado": "issued"}', timestamp)

    assert verify_webhook_signature(b'{"estado": "error"}', signature, timestamp, _SECRET) is False


def test_timestamp_fuera_de_la_ventana_de_tolerancia_devuelve_false():
    """Protección de replay (ver SECURITY.md de arca-service): un webhook legítimo
    capturado en tránsito y reenviado más tarde tiene que rechazarse, aunque la firma
    en sí siga siendo matemáticamente válida."""
    payload = b'{"estado": "issued"}'
    timestamp_viejo = str(int(time.time()) - 600)  # 10 minutos atrás
    signature = _firmar(payload, timestamp_viejo)

    assert verify_webhook_signature(payload, signature, timestamp_viejo, _SECRET) is False


def test_timestamp_justo_dentro_de_la_ventana_devuelve_true():
    payload = b'{"estado": "issued"}'
    timestamp = str(int(time.time()) - 200)  # dentro de los 300s default
    signature = _firmar(payload, timestamp)

    assert verify_webhook_signature(payload, signature, timestamp, _SECRET) is True


def test_tolerance_seconds_configurable():
    payload = b'{"estado": "issued"}'
    timestamp = str(int(time.time()) - 200)
    signature = _firmar(payload, timestamp)

    assert (
        verify_webhook_signature(payload, signature, timestamp, _SECRET, tolerance_seconds=60)
        is False
    )


def test_timestamp_no_numerico_devuelve_false_no_levanta():
    payload = b'{"estado": "issued"}'
    assert verify_webhook_signature(payload, "cualquier-firma", "no-es-un-numero", _SECRET) is False


def test_timestamp_futuro_tambien_se_rechaza():
    """`abs(...)` — no solo timestamps VIEJOS: uno del futuro (reloj del emisor
    desincronizado, o directamente fabricado) tampoco debería colar."""
    payload = b'{"estado": "issued"}'
    timestamp_futuro = str(int(time.time()) + 600)
    signature = _firmar(payload, timestamp_futuro)

    assert verify_webhook_signature(payload, signature, timestamp_futuro, _SECRET) is False
