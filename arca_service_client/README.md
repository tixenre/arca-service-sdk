# arca-service-client

Cliente HTTP oficial para [arca-service](https://github.com/tixenre/arca-service)
(facturación electrónica ARCA/AFIP) — mTLS + API key, sin depender de Django, Celery ni
`saas-core`. Un método por endpoint, verificado contra `apps/arca/api.py`/
`apps/arca/schemas.py` reales de ese repo.

## Instalación

```
pip install "arca-service-client @ git+https://github.com/tixenre/arca-service-sdk.git@arca-service-client-vX.Y.Z#subdirectory=arca_service_client"
```

## Antes de empezar: conseguir `client_cert_path`/`client_key_path`/`api_key`

Este README asume que ya tenés los tres — son la identidad de TU producto
como integrador (ganche/inmo/rambla/...), no la credencial AFIP de ninguna
org particular (eso es un paso aparte, ver "Onboarding de una credencial"
más abajo). Conseguirlos hoy es un paso manual que hace un operador de
arca-service, no self-serve: ver el checklist completo (creación del
`Client`, emisión del certificado mTLS, generación de la API key) en
[`SECURITY.md`](https://github.com/tixenre/arca-service/blob/main/SECURITY.md#2-checklist-de-onboarding-de-un-client-nuevo)
del repo de arca-service, sección 2.

## Uso

```python
from datetime import date
from decimal import Decimal

from arca_service_client import (
    ArcaServiceClient,
    Alicuota,
    Concepto,
    CondicionIva,
    DocTipo,
    ComprobanteInput,
)

client = ArcaServiceClient(
    base_url="https://arca.tudominio.com",
    client_cert_path="/etc/ganche/arca-client.crt",
    client_key_path="/etc/ganche/arca-client.key",
    api_key="...",
)

emision = client.emitir_comprobante(
    "org-123",
    ComprobanteInput(
        idempotency_key="factura-8231",
        concepto=Concepto.PRODUCTOS,
        emisor_condicion_iva=CondicionIva.RESPONSABLE_INSCRIPTO,
        receptor_doc_tipo=DocTipo.DNI,
        receptor_doc_nro="12345678",
        receptor_condicion_iva=CondicionIva.CONSUMIDOR_FINAL,
        fecha=date.today(),
        importe_neto=Decimal("1000.00"),
        alicuota_unica=Alicuota.IVA_21,
    ),
)
print(emision.estado)  # "pending" — todavía no hay CAE

# Pollear hasta que deje de estar pending, o esperar el webhook.
emision = client.get_comprobante("org-123", "factura-8231")
```

O como context manager, para que la conexión se cierre sola:

```python
with ArcaServiceClient(...) as client:
    client.emitir_comprobante(...)
```

## Emisión: siempre asincrónica

`emitir_comprobante`/`emitir_nota_credito` responden `estado="pending"` de inmediato —
arca-service todavía no le pidió el CAE a AFIP. El resultado real llega por:

- **Polling**: `client.get_comprobante(external_ref, idempotency_key)` hasta que
  `estado` sea `"issued"` (con `numero`/`cae`/`cae_vencimiento`/`qr_url`) o `"error"`
  (con `errores`). Siempre disponible, es la fuente de verdad.
- **Webhook** (opcional, si tu `Client` configuró `webhook_url` en arca-service): un
  `POST` a tu URL con el mismo shape de `EmisionResult`, firmado — ver abajo.

## `idempotency_key`

Tiene que ser determinístico por operación real (no un valor random generado en cada
intento) — reintentar el MISMO request con la MISMA key devuelve la emisión ya
existente en vez de duplicarla. Si reintentás con la misma key pero datos DISTINTOS,
`emitir_comprobante` levanta `IdempotencyConflictError` (409).

## Verificar la firma de un webhook

```python
from arca_service_client import verify_webhook_signature

# en tu endpoint que recibe el webhook:
if not verify_webhook_signature(
    payload=request_body_crudo,  # bytes, SIN reserializar
    signature=request.headers["X-Arca-Signature"],
    timestamp=request.headers["X-Arca-Timestamp"],
    secret=tu_webhook_secret,
):
    return Response(status=401)
```

Un webhook sin verificar es indistinguible de uno falsificado por cualquiera que
conozca la URL — verificalo SIEMPRE antes de procesar el payload.

## Errores

Todo error HTTP (status >= 400) de arca-service se levanta como una subclase tipada de
`ArcaServiceError` — `except ArcaServiceError` atrapa cualquiera, o discriminá por
subtipo:

| Excepción | Status | Cuándo |
|---|---|---|
| `NotFoundError` | 404 | El recurso no existe para esta org |
| `IdempotencyConflictError` | 409 | Misma `idempotency_key`, datos distintos |
| `ValidationError` | 422 | Regla de negocio rechazada (propia o de AFIP) |
| `RateLimitedError` | 429 | Límite de requests excedido — `.retry_after` en segundos |
| `AfipUnavailableError` | 502 | AFIP no respondió — transitorio, reintentable con backoff |
| `ServiceNotReadyError` | 503 | arca-service no terminó de arrancar |
| `ArcaServiceServerError` | 500 (o cualquier otro) | Bug del lado del servidor |

Fallas de TRANSPORTE (timeout, DNS, conexión rechazada) NO se envuelven — se dejan
propagar como excepciones nativas de `httpx` (`httpx.TimeoutException`,
`httpx.ConnectError`, etc.), porque "el servidor respondió que no" y "ni pudimos
preguntarle" son dos causas con remedios distintos.

## Onboarding de una credencial

Dos caminos hacia una `ArcaCredential` en arca-service, según qué tenga tu integrador:

- **Sin certificado todavía**: `generar_csr` (arca-service genera la clave privada y un
  CSR) seguido de `completar_credencial` con el `.crt` que firmó AFIP.
- **Con certificado y clave propios**: `importar_credencial` — la clave privada viaja
  sellada extremo a extremo (RSA-OAEP + AES-256-GCM contra la clave pública vigente de
  arca-service, nunca en claro en el body).

## Licencia

Proprietary — uso restringido a integradores autorizados de arca-service.
