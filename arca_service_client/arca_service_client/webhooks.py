"""arca_service_client.webhooks — verificación de la firma de un webhook de arca-service.

Puerto EXACTO del esquema de firma de `apps/arca/tasks.py::deliver_webhook_task`
(tixenre/arca-service): HMAC-SHA256 con `Client.webhook_secret` sobre
`timestamp.encode() + b"." + body` — el timestamp entra DENTRO del material firmado, no
alcanza con concatenarlo del lado del integrador. Ver SECURITY.md de arca-service:
"Verificar la firma HMAC-SHA256 de cada webhook... antes de procesar el payload — un
webhook sin verificar es indistinguible de uno falsificado por cualquiera que conozca la
URL", y "Rechazar un webhook cuyo X-Arca-Timestamp esté fuera de una ventana de
tolerancia razonable (recomendado: 5 minutos) — protección de replay: sin este chequeo,
un webhook legítimo capturado en tránsito se puede reenviar más tarde y la firma sigue
verificando OK, porque el HMAC por sí solo no expira."."""

from __future__ import annotations

import hashlib
import hmac
import time

_TOLERANCE_SECONDS_DEFAULT = 300  # 5 minutos — mismo valor que recomienda SECURITY.md


def verify_webhook_signature(
    payload: bytes,
    signature: str,
    timestamp: str,
    secret: str,
    *,
    tolerance_seconds: int = _TOLERANCE_SECONDS_DEFAULT,
) -> bool:
    """`True` si `signature` (header `X-Arca-Signature`) es una firma HMAC-SHA256 válida
    de `payload` (el body CRUDO del webhook, sin re-serializar) con `timestamp` (header
    `X-Arca-Timestamp`) y `secret` (`Client.webhook_secret`, generado/rotado desde
    /admin/ de arca-service) — Y `timestamp` cae dentro de `tolerance_seconds` del reloj
    local. `False` ante CUALQUIER problema (firma que no matchea, timestamp fuera de
    ventana, timestamp no numérico) — nunca levanta, para que el caller pueda hacer
    simplemente `if not verify_webhook_signature(...): return HttpResponse(status=401)`
    sin un try/except propio.

    `payload` tiene que ser el body EXACTO tal cual llegó (bytes crudos) — reserializar
    el JSON antes de verificar (ej. `json.dumps(json.loads(body))`) puede cambiar
    espaciado/orden de claves y romper la firma aunque el contenido "sea el mismo"."""
    try:
        ts = int(timestamp)
    except (TypeError, ValueError):
        return False
    if abs(int(time.time()) - ts) > tolerance_seconds:
        return False

    signed_payload = timestamp.encode() + b"." + payload
    expected = hmac.new(secret.encode(), signed_payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)
