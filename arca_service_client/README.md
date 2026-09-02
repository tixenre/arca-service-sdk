# arca-service-client

Cliente HTTP oficial para [arca-service](https://github.com/tixenre/arca-service)
(facturación electrónica ARCA/AFIP) — mTLS + API key, sin dependencias pesadas (nada de
frameworks web ni colas de tareas). Un método por endpoint, uno a uno con la API real.

Modelo Cliente/Plataforma: `Cliente` es el CUIT/CUIL dueño real de la facturación;
`Plataforma` sos VOS, el integrador — identidad puramente técnica, sin relación de
billing con arca-service. Un mismo Cliente puede operar a través de varias Plataformas
distintas con UNA sola credencial AFIP compartida.

## Instalación

```
pip install "arca-service-client @ git+https://github.com/tixenre/arca-service-sdk.git@main#subdirectory=arca_service_client"
```

Todavía no hay ningún tag `arca-service-client-vX.Y.Z` publicado -- `@main`
es lo que de verdad instala hoy (probado en un venv limpio: import,
`__version__`, y el CLI completo). Apenas exista un tag, preferirlo a
`@main` para fijar una versión reproducible en vez de seguir la punta de
la rama:

```
pip install "arca-service-client @ git+https://github.com/tixenre/arca-service-sdk.git@arca-service-client-vX.Y.Z#subdirectory=arca_service_client"
```

## Antes de empezar: conseguir tus credenciales

### Sin invite todavía: `arca-service-client request-invite`

Si nadie te pasó un invite code todavía, no hace falta que se lo pidas por
otro canal (Slack, mail) — este mismo CLI lo dispara:

```
arca-service-client request-invite --base-url https://arca.mancino.dev
```

Sin flags te va a preguntar nombre/slug/email de contacto de forma
interactiva (o pasalos con `--name`/`--slug`/`--contact-email`/`--message`).
Público, sin auth, sin nada criptográfico — a diferencia de `login`, esto no
genera ningún par de claves ni guarda nada en disco: solo le avisa a quien
administra arca-service que alguien quiere entrar. **No hay respuesta
automática** — un operador la revisa a mano y te entrega el resultado por un
canal seguro (nunca por acá, y no hay forma de saber cuánto tarda). Este paso
se documenta acá y no solo del lado de arca-service porque ese repo es
privado — `arca-service-sdk` es lo único público que un integrador nuevo
puede leer sin acceso a nada interno.

Con `--con-csr` te ahorrás un paso: el CLI genera tu par RSA + CSR ACÁ (la
clave privada nunca sale de tu máquina, igual que `login`) y manda el CSR
junto con la solicitud. Si se aprueba, te entregan la Plataforma ya
aprovisionada (API key + certificado) en vez de un invite code — cerrá el
trámite con `completar-signup` cuando te llegue:

```
arca-service-client request-invite --base-url https://arca.mancino.dev --con-csr
# ... esperás a que te entreguen el certificado por un canal seguro ...
arca-service-client completar-signup --cert certificado.crt --api-key <api-key>
```

Sin `--con-csr`, `request-invite` sigue funcionando exactamente igual que
antes — es opcional, no un requisito nuevo.

### Self-serve (recomendado): `arca-service-client login`

Este paquete YA ES el CLI (mismo `pip install` de arriba, sin nada extra) —
mismo patrón que `stripe login`/`gh auth login`/`aws configure`: un comando,
las credenciales quedan guardadas solas, tu código nunca vuelve a tocar un
PEM a mano.

```
arca-service-client login --base-url https://arca.mancino.dev --invite <código>
```

**Sin instalar nada:** `https://arca.mancino.dev/signup?invite=<código>` hace lo mismo
adentro del navegador — genera el par RSA y el CSR con WebCrypto, nunca los manda a
ningún lado hasta tenerlos listos, y descarga la clave privada ANTES de llamar a la API
(que nunca la ve, ni un instante). Mismo resultado final (Plataforma + API key +
certificado), sin `pip install` ni Python de por medio — útil si quien integra no tiene
Python a mano, o no quiere instalar este paquete solo para el alta inicial. Sin
`?invite=` en la URL, la misma página cubre el camino de `request-invite --con-csr` de
arriba: la solicitud queda pendiente de revisión igual que si la mandaras por acá.

**Antes de guardar cualquier certificado/clave en cualquier lugar, confirmá que
son un par válido:** `https://arca.mancino.dev/signup/verificar` hace ese chequeo en el
navegador (tampoco manda nada a ningún lado) — sirve para lo que hayas generado por
cualquier medio, no solo por esta página, y no solo en el momento del alta: copiar
credenciales a mano hacia donde vayan a vivir (env vars, un gestor de secretos) puede
corromper un carácter sin que se note, y el error recién aparece después, como un fallo
de TLS sin contexto. Si preferís no salir del código: `ArcaServiceClient(...)` hace este
mismo chequeo solo, apenas lo construís — un par que no corresponde levanta
`CredentialsInvalidError` de una, en vez de fallar recién en el primer request.

El invite code te lo entrega quien administra arca-service por un canal seguro — es de
un solo uso y vence, así que pedilo con `request-invite` de arriba si todavía no tenés
uno vigente. `login` genera tu par RSA + CSR **en tu propia máquina** (la clave privada
nunca sale de ahí, ni un instante — arca-service solo recibe el CSR, información
pública) y guarda todo en `~/.config/arca-service/` (`chmod 0600`). De ahí en más:

```python
from arca_service_client import ArcaServiceClient

client = ArcaServiceClient()  # sin argumentos -- lee el perfil que guardó login
```

`--profile <nombre>` en `login`/`ArcaServiceClient(profile=...)` si necesitás
más de una identidad guardada (ej. dos Plataformas, o distintos ambientes).
`arca-service-client whoami` te muestra qué perfil tenés activo y cuándo
vence tu certificado.

**Esto es para desarrollo local.** En producción (un container no tiene "tu"
`~/.config`) seguís pasando los cuatro explícitos — env vars, tu secret
manager — como se documenta más abajo.

### Manual (alternativa)

Quien administra arca-service también puede darte de alta a mano en vez de
mandarte un invite code. El resultado es el mismo trío
(`client_cert_path`/`client_key_path`/`api_key`) que `login` guarda solo;
con este camino los recibís vos a mano y los pasás explícitos:

```python
client = ArcaServiceClient(
    base_url="https://arca.mancino.dev",
    client_cert_path="/etc/mi-plataforma/arca-client.crt",
    client_key_path="/etc/mi-plataforma/arca-client.key",
    api_key="...",
)
```

Guardalos en un gestor de secretos compartido (nunca en un archivo
commiteado ni en un log). Un certificado/clave que no correspondan levantan
`CredentialsInvalidError` apenas construís el cliente (ver la nota sobre
`https://arca.mancino.dev/signup/verificar` más arriba) — no hace falta esperar al
primer request para enterarte.

## Uso

```python
from decimal import Decimal

from arca_service_client import (
    ArcaServiceClient,
    Concepto,
    ComprobanteInput,
    ItemFactura,
    Receptor,
)

client = ArcaServiceClient(
    base_url="https://arca.mancino.dev",
    client_cert_path="/etc/mi-plataforma/arca-client.crt",
    client_key_path="/etc/mi-plataforma/arca-client.key",
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
        receptor=Receptor(dni="12345678"),
        items=[
            ItemFactura(descripcion="Consultoría", iva="21", precio_unitario=Decimal("1000.00")),
        ],
    ),
)
print(emision.estado)  # "pending" — todavía no hay CAE

# Pollear hasta que deje de estar pending, o esperar el webhook.
emision = client.get_comprobante(onboarding.external_ref, "factura-8231")
```

`items` es la única fuente de los importes -- no hay un `importe_neto` aparte para
reconciliar. Cada `ItemFactura` lleva `iva` como el porcentaje en string (`"21"`,
`"10.5"`, `"0"`, o `"exento"`/`"no_gravado"`) y, en vez de `precio_unitario`, puede
llevar `precio_final` (con IVA incluido) — nunca los dos juntos. `receptor` identifica a
quién se le factura con exactamente una forma: `Receptor(cuit=...)`, `Receptor(dni=...,
nombre=...)` o `Receptor(consumidor_final=True)`.

**No mandes `fecha` salvo que necesites una distinta a hoy.** Es opcional: si la omitís,
la pone el servidor, con el día argentino. Mandar `date.today()` "para ser explícito" es
justamente lo que conviene evitar — en un proceso que corre en UTC eso ya es mañana a
partir de las 21 hora argentina, así que un reintento que cruce esa hora manda un
payload distinto con la misma `idempotency_key` y se lleva un `IdempotencyConflictError`
(409) en vez de la emisión que ya existía.

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
        base_url="https://arca.mancino.dev",
        client_cert_path="/etc/mi-plataforma/arca-client.crt",
        client_key_path="/etc/mi-plataforma/arca-client.key",
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
facturación), `set_bonificado(external_ref, True)` exime a ESE
Cliente, usado A TRAVÉS de TU Plataforma, de pagar su propia suscripción — no afecta su
vínculo con ninguna otra Plataforma. Activar un vínculo nuevo está sujeto a un límite de
seguridad configurado del lado de arca-service (`BonificadoLimiteError`, 409, ver
"Errores" abajo); desactivar nunca choca contra el límite.

## Datos de facturación: `set_facturacion`

`set_facturacion(external_ref, iibb="901-123456-7", nombre_comercial="La Esquina")`
configura, una sola vez por Cliente, lo único del emisor que el padrón de AFIP no
tiene y que se usa al renderizar sus comprobantes (`.html`/`.pdf`/`.imagen`). Razón
social y domicilio no se aceptan acá: los trae el padrón, no hay dos fuentes para el
mismo dato. Los dos parámetros son opcionales — pasá solo el que quieras actualizar; el
`FacturacionResult` que vuelve trae en `None` cualquiera de los dos que nunca se haya
configurado (no en `""`).

## Emisión: siempre asincrónica

`emitir_comprobante`/`emitir_nota_credito` responden `estado="pending"` de inmediato —
arca-service todavía no le pidió el CAE a AFIP. El resultado real llega por:

- **Polling**: `client.get_comprobante(external_ref, idempotency_key)` hasta que
  `estado` sea `"issued"` (con `comprobante.numero`/`cae`/`cae_vencimiento`/`qr_url`) o
  `"error"` (con `errores`). Siempre disponible, es la fuente de verdad.
- **Webhook** (opcional, si tu Plataforma configuró `webhook_url` en arca-service): un
  `POST` a tu URL con el mismo shape de `EmisionResult`, firmado — ver abajo.

`EmisionResult` anida tres bloques -- `comprobante` (qué es: tipo, letra, número...),
`importes` (los seis montos + moneda/cotización) y `receptor` (a quién se le facturó) --
en vez de traerlos todos planos:

```python
emision = client.get_comprobante(onboarding.external_ref, "factura-8231")
emision.estado                  # "pending" / "issued" / "error"
emision.comprobante.tipo        # "factura" / "nota_credito" / "nota_debito"
emision.comprobante.letra       # "B" -- None mientras está pending
emision.comprobante.numero      # 42 -- None mientras está pending
emision.importes.total          # Decimal("1210.00")
emision.receptor.nombre         # "Juan Pérez"
```

**Mirá `estado`, no un importe, para saber si ya está listo.** `importes` se calcula
desde que la emisión se crea (no cuando AFIP contesta), así que no hay ningún importe en
cero que sirva de proxy de "todavía pending" — si tu código llegó a usar ese atajo, ahora
lee plata de verdad y no se entera por una excepción.

`observaciones` (lista de strings, o `None`): comentarios de AFIP sobre un comprobante
que SÍ autorizó (ej. el documento del receptor no figura en el padrón, una fecha al
límite) — a diferencia de `errores`, no bloquean nada ni cambian `estado`. Vale la pena
mostrárselos a quien emitió en vez de descartarlos.

`errores` (con `estado="error"`) es una lista de `AfipErrorDetail` (`codigo`/`mensaje`,
el código de rechazo de AFIP tal cual, sin masticar) — mismo objeto que
`AfipRechazoError.afip` para el rechazo síncrono en un `preview_comprobante`.

### Listar: `listar_comprobantes`

`listar_comprobantes(external_ref)` trae todo lo que este Cliente tiene
emitido/pendiente/en error, más nuevo primero — mismo shape de `EmisionResult` por
ítem. Filtrable por `estado`/`tipo`/`creado_desde`/`creado_hasta`/`receptor_cuit`,
paginado con `limit` (50 default, 200 máximo)/`offset`:

```python
pagina = client.listar_comprobantes(
    onboarding.external_ref, estado="issued", limit=50, offset=0
)
pagina.count           # total que matchea el filtro, no `len(pagina.items)`
pagina.items[0].estado
```

`receptor_cuit` (con guiones o pelado, sin exigir dígito verificador) sirve para
armar "tus facturas" del lado de tu propio producto -- alcanza también filas
`pending`/`error`, no solo `issued`. Solo encuentra lo emitido con CUIT: un receptor
por DNI o consumidor final nunca aparece filtrando así.

`creado_desde`/`creado_hasta` filtran por cuándo se PIDIÓ la emisión, no por la fecha
fiscal del comprobante. Sin resultados es una lista vacía, nunca un 404.

### Preview renderizado: `preview_comprobante_html`/`_pdf`/`_imagen`

Mismo patrón que `get_comprobante_html`/`_pdf`/`_imagen` (ver "Vista embebible" abajo),
pero ANTES de emitir: nada se persiste, y el resultado viene marcado como vista previa
(`"SIN EMITIR — NO VÁLIDO"`, sin número real). Sirve para mostrarle a alguien cómo va a
quedar el comprobante antes de confirmar una acción fiscal irreversible — complementa a
`preview_comprobante` (que solo da los importes), no lo reemplaza. Mismos tres métodos
para `_nota_credito_`/`_nota_debito_`:

```python
html = client.preview_comprobante_html(onboarding.external_ref, comprobante)
pdf = client.preview_nota_credito_pdf(onboarding.external_ref, nota_credito)
```

### Sesión embebida: facturar en un `<iframe>`

`crear_sesion_embebida_comprobante`/`crear_sesion_embebida_nota_credito`/
`crear_sesion_embebida_nota_debito` son una puerta de entrada ALTERNATIVA a
`emitir_comprobante`/`emitir_nota_credito`/`emitir_nota_debito` — no las reemplazan, es
un método más. Devuelven un link para embeber en un `<iframe>` en vez de emitir de una.

```python
from arca_service_client import SesionEmbebidaInput

resultado = client.crear_sesion_embebida_comprobante(
    onboarding.external_ref,
    SesionEmbebidaInput(
        idempotency_key="factura-8231",
        concepto=Concepto.PRODUCTOS,
        items=[
            ItemFactura(descripcion="Consultoría", iva="21", precio_unitario=Decimal("1000.00")),
        ],
    ),
)
resultado.embed_url    # listo para <iframe src="...">
resultado.expires_at   # datetime UTC -- 30 min desde que se creó la sesión
```

`SesionEmbebidaInput` es el mismo body que `ComprobanteInput`, pero con `receptor`
OPCIONAL -- según lo pasés o no, cambia qué hace el iframe:

* **Sin `receptor`** (el ejemplo de arriba) -- tu Plataforma sabe cuánto facturar pero
  no a quién; el comprador completa su propio dato fiscal adentro del iframe.
* **Con `receptor`** (`SesionEmbebidaInput(..., receptor=Receptor(cuit="..."))`) -- tu
  Plataforma ya tiene el dato fiscal en su base; el iframe pasa a ser solo la pantalla
  donde el comprador mira la factura que está por salir y confirma, sin cargar nada.

El resto del payload (ítems, importes) queda fijo desde este llamado en los dos casos:
la página embebida no lo puede cambiar, y un ítem mal armado da error acá y no media
hora después con alguien mirando un iframe que no carga.
`crear_sesion_embebida_nota_credito`/`_nota_debito` exigen `comprobante_asociado`, igual
que sus equivalentes `emitir_*`.

Para embeber `embed_url` del lado del frontend -- eventos de éxito/error, qué pasa si
el comprador abandona a mitad de camino, cómo hacerlo con o sin el SDK de JS de
arca-service -- ver `INTEGRACION.md` en el repo de arca-service: esa parte vive del
lado del browser, no es código Python.

**Crear la sesión NO es idempotente, aunque la emisión sí lo sea.** Llamar dos veces con
la misma `idempotency_key` no da `IdempotencyConflictError`: devuelve un `embed_url`
nuevo las dos veces. Es a propósito -- si el comprador abandonó y vuelve mañana (el link
vive 30 minutos), lo que hace falta es otro link, no un error. De las dos sesiones sale
UN solo comprobante igual, porque la idempotencia es de la emisión y esa clave sigue
siendo la misma.

**Que el iframe termine no es lo mismo que que haya CAE.** El evento de éxito del
browser dice que el comprador terminó; el CAE lo pone AFIP después, y puede rechazar.
Confirmá siempre desde tu backend antes de dar algo por facturado -- con
`get_comprobante(external_ref, idempotency_key)` y `estado == "issued"`, o esperando el
webhook. `"pending"` todavía no terminó y `"error"` es un rechazo con el motivo en
`.errores`.

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
resultado.embed_url    # "https://arca.mancino.dev/embed/comprobantes/<token>/comprobante.html"
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

Todo error HTTP (status >= 400) de arca-service viaja como
`{"error": {"type", "code", "message", "param"?, "afip"?}}` y se levanta como una
subclase tipada de `ArcaServiceError` — `except ArcaServiceError` atrapa cualquiera, o
discriminá por `.code` (el motivo puntual, estable — lo que hay que mirar para
ramificar) o por `.type` (la decisión gruesa, son CUATRO y no crecen). `.message` es
para mostrar a una persona, nunca para ramificar.

| `type` | Excepción base | Qué hacer |
|---|---|---|
| `request` | `RequestError` | Cambiá lo que mandás (según `.code`/`.param`) y reintentá |
| `configuracion` | `ConfiguracionError` | Nada desde el código -- el dueño del CUIT tiene un trámite pendiente en el portal de AFIP |
| `afip` | `AfipError` | AFIP rechazó (no reintentes) o no contestó (reintentá) -- lo dice `.code` |
| `interno` | `InternoError` | Es del lado de arca-service -- avisale si persiste |

Un `.code` que este SDK todavía no conoce cae en la excepción genérica de su `.type` de
todos modos (con el `code` real igual accesible en `.code`) — la lista de `code` crece,
los cuatro `type` no. Algunos `code` puntuales tienen su propia subclase con nombre,
para no obligarte a mirar `.code` a mano en los casos más comunes:

| Excepción | `type` | Status típico | Cuándo |
|---|---|---|---|
| `CredentialsRejectedError` | `request` | 401 / 403 | arca-service rechazó las credenciales de TU Plataforma: API key inexistente, revocada o vencida, Plataforma desactivada, o el certificado mTLS que no llegó o no sirve. **No es un problema del payload** — corregir el request no cambia nada. Los dos casos dan el mismo error a propósito, así que desde afuera no se puede saber cuál de las dos capas falló |
| `NotFoundError` | `request` | 404 | El recurso no existe para este Cliente, o el `external_ref` no existe / tu Plataforma no está autorizada contra él |
| `IdempotencyConflictError` | `request` | 409 | Misma `idempotency_key`, datos distintos |
| `RateLimitedError` | `request` | 429 | Límite de requests excedido — `.retry_after` en segundos |
| `PuntoVentaNoHabilitadoError` | `configuracion` | 422 | El punto de venta no está habilitado en AFIP (bloqueado, dado de baja, o no electrónico) — se arregla en el portal de AFIP, no cambiando el request |
| `NotaExcedeComprobanteError` | `request` | 422 | La nota de crédito/débito acredita más de lo que queda disponible en la factura que referencia (`.param == "comprobante_asociado"`) |
| `AfipRechazoError` | `afip` | 422 | AFIP rechazó el comprobante — `.afip` trae los códigos de rechazo sin masticar (`AfipErrorDetail.codigo`/`.mensaje`), no reintentable |
| `AfipUnavailableError` | `afip` | 502 | AFIP no contestó o contestó en un formato inesperado — transitorio, reintentable con backoff |
| `ServicioNoDisponibleError` | `interno` | 503 | arca-service no puede completar ESTE request puntual (ej. el renderizador de PDF/imagen está caído) — **no significa que la emisión haya fallado**, el CAE sigue ahí |
| `BonificadoLimiteError` | `configuracion` | 409 | `set_bonificado` chocó contra el límite de seguridad de tu Plataforma — pedile a arca-service que lo suba, no es un error tuyo ni del Cliente |
| `CsrYaExisteError` | `request` | 409 | `generar_csr` chocó con un CSR pendiente ya generado antes para este Cliente — pasá `regenerar=True` para descartarlo y arrancar de cero |
| `CredencialYaActivaError` | `request` | 409 | `generar_csr` chocó con una credencial ya activa para este Cliente — pasá `regenerar=True` para reemplazarla |

`IdempotencyConflictError`, `BonificadoLimiteError`, `CsrYaExisteError` y
`CredencialYaActivaError` comparten status code (409) pero NO significado — cuatro
conflictos de negocio sin relación entre sí, cada uno con su propio `code` (y por lo
tanto su propio subtipo) para que discriminar por `except` no los confunda.

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

### Con certificado y clave propios: `arca-service-client import`

Si un Cliente ya tiene certificado+clave AFIP de antes (otro sistema, otra
Plataforma, una migración), no hace falta pasar por `generar_csr` — importalos
directo:

```
arca-service-client import --cuit 20301234563 --cert cliente.crt --key cliente.key
```

Requiere que ya hayas corrido `login` (o configurado tu perfil a mano) —
`import` actúa en nombre de TU Plataforma, así que necesita tu mTLS/API key
en mano, igual que cualquier otro comando autenticado. `--cert`/`--key` son
**rutas de archivo**, nunca el contenido como argumento — un valor tan
sensible como una clave privada no debería aparecer en tu historial de shell
ni en `ps aux`. Si la clave está cifrada, pasá `--key-password` o, para no
escribir la passphrase en un argumento tampoco, `--key-password-prompt` (la
pide de forma interactiva y oculta). `--point-of-sale`: si ya sabés cuál está
habilitado para este certificado (default `0` — se puede asignar después con
`listar_puntos_de_venta`).

La clave nunca viaja en claro — `import` la sella (mismo mecanismo que
`importar_credencial` de arriba) antes de mandarla.

## Conexión AFIP embebida (iframe): `crear_conexion_afip_embed_token`

Un TERCER camino hacia una `ArcaCredential`, alternativo a los dos de arriba: en vez de
que TU backend orqueste `generar_csr`/`completar_credencial`/`importar_credencial` paso a
paso, tu cliente final los completa él mismo en una página que sirve arca-service —
mismo patrón "hosted page" que `crear_embed_token` (comprobantes, ver arriba), pero acá
es un flujo INTERACTIVO completo, no una vista de solo lectura. Útil cuando no querés
construir vos la UI de "subí tu certificado" (o tu cliente final ya tiene varios
certificados AFIP y necesita elegir cuál usar).

```python
resultado = client.crear_conexion_afip_embed_token("cliente-1")
resultado.embed_url    # "https://arca.mancino.dev/embed/conexion-afip/<token>"
resultado.expires_at   # datetime UTC -- 30 min desde la emisión del token, por default
```

`embed_url` no requiere mTLS ni API key para abrirse -- listo para `<iframe
src="...">` directo en tu frontend. Mismo trade-off de vida corta que
`crear_embed_token` (sin revocación individual, la ventana corta ES el
control) -- tratalo como un secreto de corta vida, no lo loggees ni lo
guardes más tiempo del que dure.

A diferencia de `crear_embed_token` (una vista estática, nada que
"terminar"), esta página SÍ tiene un final: apenas tu cliente completa su
conexión, manda `window.parent.postMessage({type: "arca:conexion_completa"},
"*")` — escuchalo en tu frontend para reaccionar (cerrar el iframe/modal,
refrescar tu propio estado) sin tener que hacer polling contra tu backend:

```javascript
window.addEventListener("message", (event) => {
  if (event.data?.type === "arca:conexion_completa") {
    // cerrar el iframe, refrescar tu propio estado, etc.
  }
});
```

### ¿Ya tenés tu propio motor de facturación?

Leé esto ANTES de conectar `crear_conexion_afip_embed_token`/`importar_credencial` a un
sistema que ya factura por su cuenta (integración directa con WSAA/WSFE, cert propio,
tabla propia de emisores) — es la pregunta que conviene resolver primero, no a mitad de
la integración.

**El flujo embebido no es una forma de "conseguir un certificado" para usar en otro
lado.** Por diseño, arca-service NUNCA devuelve el cert/clave en claro una vez
guardados — ni al integrador, ni siquiera a sí mismo fuera del propio flujo de emisión
(ver "Onboarding de una credencial" arriba: la clave viaja sellada extremo a extremo y
se queda cifrada en reposo). No hay un endpoint que la "saque" para pegarla en tu propio
motor. Esto es intencional, no una limitación transitoria — es la misma garantía de
seguridad que hace seguro embeber el flujo en un iframe de tu frontend en primer lugar.

**Consecuencia práctica:** adoptar arca-service para un emisor puntual significa que ESE
emisor factura A TRAVÉS de arca-service de ahí en más (`emitir_comprobante`/
`emitir_nota_credito`/etc.) — no que arca-service te presta un certificado para seguir
emitiendo vos con tu propia integración. Los dos caminos reales para migrar sin
"big bang":

- **Emisores nuevos por acá, los viejos se quedan donde están** — mientras no migres
  un emisor existente, tu sistema actual sigue facturando por él sin ningún cambio.
  Cero riesgo, cero urgencia.
- **Migrar un emisor existente** — importás su cert+clave ya vigente
  (`importar_credencial`, o el propio dueño lo hace vía el embed) y, desde ese momento,
  TODAS sus emisiones nuevas pasan a `emitir_comprobante`/etc. Tu sistema viejo deja de
  facturar para ese emisor — no los dos en paralelo: dos numeraciones independientes
  para el mismo punto de venta ante AFIP no es un problema cosmético, AFIP lo rechaza o
  genera huecos de numeración reales. Antes de migrar un emisor con reglas propias
  (perfiles fiscales múltiples, notas de crédito con lógica custom, aprobaciones
  manuales, etc.), confirmá que arca-service cubre lo que ese emisor necesita — el resto
  de este README documenta la superficie completa.

## Licencia

Proprietary — uso restringido a integradores autorizados de arca-service.
