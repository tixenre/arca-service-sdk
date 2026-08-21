# arca-service-client

Cliente HTTP oficial para [arca-service](https://github.com/tixenre/arca-service)
(facturación electrónica ARCA/AFIP) — mTLS + API key, sin depender de Django, Celery ni
`saas-core`. Un método por endpoint, verificado contra el router/controllers/schemas
reales de Phoenix (`arca_service_phx/`) en ese repo.

Modelo Cliente/Plataforma: `Cliente` es el CUIT/CUIL dueño real de la facturación;
`Plataforma` sos VOS, el integrador (ganche/inmo/rambla/...) — identidad puramente
técnica, sin relación de billing con arca-service. Un mismo Cliente puede operar a
través de varias Plataformas distintas con UNA sola credencial AFIP compartida.

## Instalación

```
pip install "arca-service-client @ git+https://github.com/tixenre/arca-service-sdk.git@arca-service-client-vX.Y.Z#subdirectory=arca_service_client"
```

## Antes de empezar: conseguir `client_cert_path`/`client_key_path`/`api_key`

Este README asume que ya tenés los tres — son la identidad de TU Plataforma
como integrador (ganche/inmo/rambla/...), no la credencial AFIP de ningún
Cliente particular (eso es un paso aparte, ver "Onboarding de una credencial"
más abajo). Conseguirlos hoy es un paso manual que hace un operador de
arca-service, no self-serve: ver el checklist completo (creación de la
Plataforma, emisión del certificado mTLS, generación de la API key) en
[`SECURITY.md`](https://github.com/tixenre/arca-service/blob/main/SECURITY.md#2-checklist-de-onboarding-de-un-client-nuevo)
del repo de arca-service, sección 2 (el anchor sigue diciendo "Client" — es el
nombre que Django, todavía corriendo en paralelo, le da al mismo concepto;
ver la nota de terminología al principio de esa sección).

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

# Primer llamado siempre: resuelve (o crea) el Cliente dueño de este CUIT, y
# crea/reactiva el vínculo de TU Plataforma con él. Guardá `external_ref` vos
# (es estable para este CUIT) — no hace falta llamar `por_cuit` de nuevo en
# cada request, solo la primera vez que ves un CUIT nuevo.
onboarding = client.por_cuit("20301234563")

emision = client.emitir_comprobante(
    onboarding.external_ref,
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
emision = client.get_comprobante(onboarding.external_ref, "factura-8231")
```

O como context manager, para que la conexión se cierre sola:

```python
with ArcaServiceClient(...) as client:
    client.emitir_comprobante(...)
```

## Consumidor async (FastAPI): `AsyncArcaServiceClient`

Misma API exacta, con `async`/`await` — mismos nombres de método, mismos tipos de
retorno, sobre `httpx.AsyncClient` en vez de `httpx.Client`:

```python
from arca_service_client import AsyncArcaServiceClient

async def facturar(cuit: str, comprobante: ComprobanteInput):
    async with AsyncArcaServiceClient(
        base_url="https://arca.tudominio.com",
        client_cert_path="/etc/rambla/arca-client.crt",
        client_key_path="/etc/rambla/arca-client.key",
        api_key="...",
    ) as client:
        onboarding = await client.por_cuit(cuit)
        return await client.emitir_comprobante(onboarding.external_ref, comprobante)
```

Fuera de un `async with`, cerrala vos con `await client.aclose()` (no `.close()` —
mismo nombre que usa `httpx.AsyncClient` para lo mismo). No hay ninguna diferencia de
comportamiento/contrato entre las dos variantes más allá de sync vs. async — todo lo
demás de este README (idempotencia, errores, onboarding de credencial, webhooks) aplica
igual a las dos.

## Onboarding del Cliente: `por_cuit`

`por_cuit(cuit)` es el único lugar donde el CUIT se acepta como input en vez de
resolverse por `external_ref` — el resto de la API nunca vuelve a mencionar un CUIT.
Idempotente en dos sentidos: si el CUIT no existe, crea el `Cliente`; si ya existe
(porque otra Plataforma lo onboardeó antes), solo crea/reactiva TU vínculo con él —
nunca un segundo `Cliente` para el mismo CUIT. El `external_ref` que devuelve es estable
para ese CUIT: guardalo vos, no hace falta re-onboardear en cada request.

## Bonificación cruzada: `set_bonificado`

Si tu Plataforma tiene un acuerdo con arca-service (ej. un plan propio que ya incluye
facturación, tipo "Ganche Pro"), `set_bonificado(external_ref, True)` exime a ESE
Cliente, usado A TRAVÉS de TU Plataforma, de pagar su propia suscripción — no afecta su
vínculo con ninguna otra Plataforma. Activar un vínculo nuevo está sujeto a un límite de
seguridad configurado del lado de arca-service (`BonificadoLimiteError`, 409, ver
"Errores" abajo); desactivar nunca choca contra el límite.

## Emisión: siempre asincrónica

`emitir_comprobante`/`emitir_nota_credito` responden `estado="pending"` de inmediato —
arca-service todavía no le pidió el CAE a AFIP. El resultado real llega por:

- **Polling**: `client.get_comprobante(external_ref, idempotency_key)` hasta que
  `estado` sea `"issued"` (con `numero`/`cae`/`cae_vencimiento`/`qr_url`) o `"error"`
  (con `errores`). Siempre disponible, es la fuente de verdad.
- **Webhook** (opcional, si tu Plataforma configuró `webhook_url` en arca-service): un
  `POST` a tu URL con el mismo shape de `EmisionResult`, firmado — ver abajo.

## `idempotency_key`

Tiene que ser determinístico por operación real (no un valor random generado en cada
intento) — reintentar el MISMO request con la MISMA key devuelve la emisión ya
existente en vez de duplicarla. Si reintentás con la misma key pero datos DISTINTOS,
`emitir_comprobante` levanta `IdempotencyConflictError` (409).

## Vista embebible (iframe): `crear_embed_token`

Un link público, de vida corta, para mostrarle un comprobante a alguien sin que tu
backend tenga que estar en el medio — mismo patrón que la "hosted invoice page" de
Stripe. Complementa a `get_comprobante_html`/`get_comprobante_pdf` (llamado del lado
servidor, con tu mTLS/API key), no los reemplaza: si tu backend ya tiene al usuario
logueado, seguí sirviendo vos ese resultado. `crear_embed_token` es para cuando el
HTML lo tiene que pedir directamente el browser (un `<iframe src="...">`) o el link se
comparte fuera de tu propia sesión autenticada.

```python
resultado = client.crear_embed_token("cliente-1", "factura-8231")
resultado.embed_url    # "https://arca.tudominio.com/embed/comprobantes/<token>/comprobante.html"
resultado.expires_at   # datetime UTC -- 30 min desde la emisión del token, por default
```

`embed_url` no requiere mTLS ni API key para abrirse — cualquiera con el link puede
verlo hasta `expires_at`, así que tratalo como un secreto de vida corta (no lo
loggees, no lo guardes más tiempo del que dure). No hay forma de revocar un token
puntual antes de que venza (mismo trade-off que un link de Stripe: la ventana corta ES
el control) — pedí uno nuevo si el anterior se filtró.

Dos errores distintos, en dos momentos distintos: `crear_embed_token` en sí levanta
`NotFoundError` (ver "Errores" abajo) si `idempotency_key` no resuelve a una emisión
tuya — eso lo ves vos, en tu backend, al pedir el token. Ya con el `embed_url` en
mano, si vence o el comprobante deja de estar `"issued"`, quien lo abra en el browser
recibe un 404 directo de arca-service — ese error no pasa por el SDK ni por vos.

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
| `NotFoundError` | 404 | El recurso no existe para este Cliente, o el `external_ref` no existe / tu Plataforma no está autorizada contra él |
| `IdempotencyConflictError` | 409 | Misma `idempotency_key`, datos distintos |
| `BonificadoLimiteError` | 409 | `set_bonificado` chocó contra el límite de seguridad de tu Plataforma — pedile a arca-service que lo suba, no es un error tuyo ni del Cliente |
| `ValidationError` | 422 | Regla de negocio rechazada (propia o de AFIP) |
| `RateLimitedError` | 429 | Límite de requests excedido — `.retry_after` en segundos |
| `AfipUnavailableError` | 502 | AFIP no respondió — transitorio, reintentable con backoff |
| `ServiceNotReadyError` | 503 | arca-service no terminó de arrancar |
| `ArcaServiceServerError` | 500 (o cualquier otro) | Bug del lado del servidor |

`IdempotencyConflictError` y `BonificadoLimiteError` comparten status code (409) pero
NO tipo — son dos conflictos de negocio sin relación, cada uno con su propio subtipo a
propósito para que discriminar por `except` no los confunda.

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
