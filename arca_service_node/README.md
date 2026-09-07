# @tixenre/arca-service-node

Cliente HTTP oficial para [arca-service](https://github.com/tixenre/arca-service)
(facturación electrónica ARCA/AFIP) desde Node/TypeScript — mTLS + API key, sin
dependencias de runtime.

Es el equivalente en Node de `arca_service_client` (Python), portado desde el mismo
contrato: mismos endpoints, mismos shapes, misma jerarquía de errores. Los payloads que
arma este paquete se comparan contra los que arma el SDK de Python en los tests, así que
"equivalente" no es una intención sino algo que se verifica en cada corrida.

> **Server-side únicamente.** Este cliente lleva el certificado mTLS y la API key de TU
> Plataforma: las dos son credenciales de servidor. En Next.js eso significa route
> handlers, server actions o `getServerSideProps` — nunca un componente de cliente. Si el
> bundle del browser llega a importar esto, las credenciales se publican.

## Instalación

```
npm install @tixenre/arca-service-node
```

Requiere Node >= 18. Cero dependencias de runtime: usa `node:https` para el mTLS y
WebCrypto para el sellado.

## Uso

```ts
import { ArcaServiceClient, Concepto } from '@tixenre/arca-service-node'

const client = new ArcaServiceClient({
  baseUrl: 'https://arca.mancino.dev',
  apiKey: process.env.ARCA_API_KEY!,
  clientCert: process.env.ARCA_CLIENT_CERT!, // el PEM, no la ruta
  clientKey: process.env.ARCA_CLIENT_KEY!,
})

// Primer llamado siempre: resuelve (o crea) el Cliente dueño de este CUIT, y crea o
// reactiva el vínculo de TU Plataforma con él. Guardá `externalRef`: es estable.
const { externalRef } = await client.porCuit('20301234563')

const emision = await client.emitirComprobante(externalRef, {
  idempotencyKey: 'factura-8231',
  concepto: Concepto.PRODUCTOS,
  receptor: { dni: '12345678' },
  items: [{ descripcion: 'Consultoría', iva: '21', precioUnitario: '1000.00' }],
})

console.log(emision.estado) // "pending" -- todavía no hay CAE
```

Las credenciales también se pueden pasar como rutas en disco, con `clientCertPath`/
`clientKeyPath`. El constructor verifica que el certificado y la clave sean un par ANTES
del primer request: si no lo son, tirá `CredentialsInvalidError` de una, en vez de fallar
más tarde en el handshake TLS con un error de OpenSSL sin contexto.

`client.close()` cierra las conexiones que quedaron abiertas por keep-alive. En un servidor
de larga vida conviene crear un cliente y reusarlo, no uno por request.

## Dos convenciones que conviene saber de entrada

**Los importes son `string`, nunca `number`.** `0.1 + 0.2 !== 0.3` y `1000.10` no es
representable en binario; en un importe fiscal eso es un error que se descubre tarde y
caro. Entran como string (`'1000.00'`) y salen como string, igual que en el JSON. Si
necesitás hacer cuentas, usá una librería de decimales y convertí en el borde.

**camelCase de este lado, snake_case en el cable.** Vos escribís `idempotencyKey`, el
servidor recibe `idempotency_key`. La traducción es explícita, campo por campo, no un
`snakeCase()` genérico: un mapeo a mano falla al compilar cuando el contrato se mueve, y
uno automático falla en silencio mandando un campo que el servidor ignora.

Por la misma razón las fechas de comprobante son strings `'YYYY-MM-DD'` y no `Date`: un
`Date` es un instante, y convertirlo a día calendario reintroduce el bug de zona horaria
que se quiere evitar. `expiresAt`, que sí es un instante, viene como `Date`.

## Antes de emitir de verdad: `habilitarCliente`

Un Cliente nuevo arranca en modo práctica: `previewComprobante`, `diagnosticarCredencial`
y `consultarPadron` andan igual, pero `emitirComprobante`/`emitirNotaCredito`/
`emitirNotaDebito` (sueltos o en lote) devuelven **422** (`ClienteEnPracticaError`) hasta
que alguien confirme que quiere facturar de verdad:

```ts
const resultado = await client.habilitarCliente(externalRef)
resultado.habilitacion  // "habilitado"
resultado.habilitadoAt  // Date -- cuándo se dio este consentimiento
resultado.primerCaeAt   // null hasta que AFIP autorice el primer comprobante real
```

Sin body -- es una confirmación, no hay nada que elegir. Idempotente: llamarlo de nuevo
sobre un Cliente ya habilitado devuelve lo mismo sin volver a sellar nada, así que un
reintento de red nunca duplica un consentimiento. Los Clientes que ya venían facturando
antes de que este paso existiera ya están habilitados — no hace falta llamar esto para
ellos.

**No hace falta si facturás a través de la sesión embebida** (ver más abajo): el iframe
hace esta misma pregunta solo, como parte de la pantalla de confirmar, sin que tu
Plataforma llame nada. Llamalo solo si tu integración factura directo con
`emitirComprobante` y compañía.

`ClienteSuspendidoError` (422) si la emisión de este Cliente está cortada -- a diferencia
del caso de arriba, eso no se destraba llamando esto ni ningún otro método: es un corte
comercial que solo reactiva un operador de arca-service.

**`habilitadoAt` no es lo mismo que "ya facturó de verdad".** Consentir es un permiso, y
un permiso ejercido contra una primera factura que AFIP rechaza no mueve `primerCaeAt`. Si
lo que te importa es si el Cliente ya tiene un comprobante real emitido, es `primerCaeAt`
el campo que hay que mirar, no `habilitadoAt`.

## Emisión: siempre asincrónica

`emitirComprobante` devuelve `estado: 'pending'` y el CAE llega después. Hay dos formas de
enterarse, y conviene tener las dos:

```ts
// 1. Pollear.
const actual = await client.getComprobante(externalRef, 'factura-8231')
if (actual.estado === 'issued') console.log(actual.cae, actual.comprobante.numero)
if (actual.estado === 'error') console.log(actual.errores)

// 2. El webhook, verificando SIEMPRE la firma antes de procesar nada.
import { verifyWebhookSignature } from '@tixenre/arca-service-node'

export async function POST(request: Request) {
  const body = await request.text() // el texto CRUDO, no .json()
  const ok = verifyWebhookSignature({
    payload: body,
    signature: request.headers.get('x-arca-signature') ?? '',
    timestamp: request.headers.get('x-arca-timestamp') ?? '',
    secret: process.env.ARCA_WEBHOOK_SECRET!,
  })
  if (!ok) return new Response(null, { status: 401 })
  // ...
}
```

Verificar sobre el body reserializado (`JSON.stringify(await request.json())`) rompe la
firma aunque el contenido "sea el mismo": cambia espaciado y orden de claves.

## No mandes `fecha` salvo que necesites una distinta a hoy

Es opcional: si la omitís, la pone el servidor, con el día argentino. Armarla desde un
proceso en UTC (`new Date().toISOString().slice(0, 10)`) ya da "mañana" a partir de las 21
hora argentina — y además cambia el payload, así que un reintento que cruce esa hora se
lleva un `IdempotencyConflictError` con la misma `idempotencyKey`.

## `layout`: los tres formatos, y cuándo `simplificada` no sirve

Los doce métodos que renderizan (`getComprobanteHtml`/`Pdf`/`Imagen` y los nueve de
preview) toman `{ layout }` -- los tres incluyen lo que AFIP exige (CAE, QR fiscal, IVA
discriminado, leyenda de Transparencia Fiscal); lo que cambia es cuánto desglose por ítem,
no la validez fiscal:

| | `'oficial'` (default) | `'detallada'` | `'simplificada'` |
|---|---|---|---|
| Formato | A4 | A4, identidad visual propia | Tarjeta 4:5 (1080×1350 px), para compartir |
| Código de producto | sí | no | no |
| Descripción + detalle | sí | sí | solo descripción |
| Cantidad | sí | sí | no |
| Unidad de medida | sí | no | no |
| Precio unitario | sí | sí | no |
| % de bonificación | sí | no | no |
| Subtotal por ítem | sí | sí (ya con la bonificación aplicada) | sí |

**`'detallada'` no es "`oficial` con más detalle" — es al revés: omite columnas** (código,
unidad de medida, % de bonificación) y las resuelve adentro del subtotal.

`'simplificada'` **rechaza** el comprobante que no le entra en vez de recortarlo: la
tarjeta tiene lugar para poco, y lo que no entra ahí no es una columna, es el ítem entero.
Devuelve **422** (`LayoutNoAptoError`, `.param === "layout"`, con cuántos ítems tiene y
cuántos entran en `.message`) si hay más de 3 ítems, o si algún ítem tiene descripción de
más de 40 caracteres, `cantidad` distinta de 1, bonificación, detalle, o una unidad de
medida que no sea la default. Si no entra, pedilo en `'oficial'` o `'detallada'`, que no
tienen límite.

**`Imagen` captura una sola página.** Un comprobante `'oficial'`/`'detallada'` con más
ítems de los que entran en un A4 sale cortado por abajo en el PNG -- el `Pdf` del mismo
comprobante no, porque ahí el renderizador pagina.

## Sesión embebida: facturar en un `<iframe>`

`crearSesionEmbebidaComprobante`/`crearSesionEmbebidaNotaCredito`/
`crearSesionEmbebidaNotaDebito` son una puerta de entrada ALTERNATIVA a
`emitirComprobante`/`emitirNotaCredito`/`emitirNotaDebito` — no las reemplazan, es un
método más. Devuelven un link para embeber en un `<iframe>` en vez de emitir de una:

```ts
const resultado = await client.crearSesionEmbebidaComprobante(externalRef, {
  idempotencyKey: 'factura-8231',
  concepto: Concepto.PRODUCTOS,
  items: [{ descripcion: 'Consultoría', iva: '21', precioUnitario: '1000.00' }],
})
resultado.embedUrl   // listo para <iframe src="...">
resultado.expiresAt  // Date -- 30 min desde que se creó la sesión
```

El body es el mismo que `emitirComprobante`, pero con `receptor` OPCIONAL -- según lo
pases o no, cambia qué hace el iframe:

* **Sin `receptor`** (el ejemplo de arriba) -- tu Plataforma sabe cuánto facturar pero no
  a quién; el comprador completa su propio dato fiscal adentro del iframe.
* **Con `receptor`** (`{ ..., receptor: { cuit: '...' } }`) -- tu Plataforma ya tiene el
  dato fiscal en su base; el iframe pasa a ser solo la pantalla donde el comprador mira la
  factura que está por salir y confirma, sin cargar nada.

**Si tenés el email del receptor, mandalo** (`{ cuit: '...', email: '...' }`): con eso el
iframe deja de pedírselo a la persona -- le muestra a dónde va la copia del comprobante y
le ofrece cambiarlo, en vez de un campo vacío pidiendo un dato que vos ya tenías. Si la
persona escribe uno distinto ahí, ese gana para cualquier factura futura a ese mismo CUIT
(de cualquier integración, no solo la tuya) -- el que vos mandaste vale solo para esta
emisión. Sin `cuit` (receptor por DNI/consumidor final) se le avisa igual a dónde va la
copia, pero no se le ofrece cambiarlo: no hay bajo qué guardar otro.

El resto del payload (ítems, importes) queda fijo desde este llamado en los dos casos: la
página embebida no lo puede cambiar, y un ítem mal armado da error acá y no media hora
después con alguien mirando un iframe que no carga. `crearSesionEmbebidaNotaCredito`/
`NotaDebito` exigen `comprobanteAsociado`, igual que sus equivalentes `emitir*`.

Para embeber `embedUrl` del lado del frontend -- eventos de éxito/error, qué pasa si el
comprador abandona a mitad de camino, cómo hacerlo con o sin el SDK de JS de arca-service
-- ver `INTEGRACION.md` en el repo de arca-service: esa parte vive del lado del browser,
no es código de este paquete.

**Crear la sesión NO es idempotente, aunque la emisión sí lo sea.** Llamar dos veces con
la misma `idempotencyKey` no da `IdempotencyConflictError`: devuelve un `embedUrl` nuevo
las dos veces -- si el comprador abandonó y vuelve mañana (el link vive 30 minutos), lo
que hace falta es otro link, no un error. De las dos sesiones sale UN solo comprobante
igual, porque la idempotencia es de la emisión y esa clave sigue siendo la misma.

**Que el iframe termine no es lo mismo que que haya CAE.** El evento de éxito del browser
dice que el comprador terminó; el CAE lo pone AFIP después, y puede rechazar. Confirmá
siempre desde tu backend antes de dar algo por facturado -- con `getComprobante(
externalRef, idempotencyKey)` y `estado === 'issued'`, o esperando el webhook.

## Errores

Todo error HTTP viaja en el mismo sobre y se levanta como una subclase de
`ArcaServiceError`. `catch` de `ArcaServiceError` atrapa cualquiera; para discriminar, mirá
`.code` (estable, para programas) o `.type` (grueso) — nunca `.message`, que está escrito
para que lo lea una persona.

| `type` | Clase base | Qué hacer |
|---|---|---|
| `request` | `RequestError` | Cambiá lo que mandás y reintentá |
| `configuracion` | `ConfiguracionError` | Nada desde el código: hay un trámite pendiente en el portal de AFIP |
| `afip` | `AfipError` | AFIP rechazó (no reintentes) o no contestó (reintentá) |
| `interno` | `InternoError` | Es del lado de arca-service |

Un `code` que este paquete todavía no conoce cae en la clase de su `type`, nunca en un
catch-all sin tipar. Algunos tienen clase propia:

| Clase | Status | Cuándo |
|---|---|---|
| `CredentialsRejectedError` | 401 / 403 | Tu API key o tu certificado mTLS: **no es un problema del payload**, corregir el request no cambia nada |
| `NotFoundError` | 404 | El recurso no existe para este Cliente, o no estás autorizado contra ese `externalRef` |
| `IdempotencyConflictError` | 409 | Misma `idempotencyKey`, datos distintos |
| `CsrYaExisteError` / `CredencialYaActivaError` | 409 | `generarCsr` sin `regenerar: true` |
| `BonificadoLimiteError` | 409 | `setBonificado` chocó contra el límite de tu Plataforma |
| `RateLimitedError` | 429 | `.retryAfter` en segundos |
| `PuntoVentaNoHabilitadoError` | 422 | Se arregla en el portal de AFIP |
| `NotaExcedeComprobanteError` | 422 | La nota acredita más de lo disponible |
| `ClienteEnPracticaError` | 422 | El Cliente todavía no confirmó que quiere facturar de verdad -- llamá `habilitarCliente` primero (no hace falta si facturás por el iframe) |
| `ClienteSuspendidoError` | 422 | La emisión de este Cliente está cortada -- no se reactiva desde la API |
| `LayoutNoAptoError` | 422 | El comprobante no entra en el `layout` pedido (`.param === "layout"`) -- pedilo en `'oficial'`/`'detallada'` |
| `AfipRechazoError` | 422 | `.afip` trae los códigos de AFIP sin masticar |
| `AfipUnavailableError` | 502 | Transitorio, reintentable con backoff |
| `ServicioNoDisponibleError` | 503 | Este request puntual; **no significa que la emisión haya fallado** |

`ClienteEnPracticaError` y `ClienteSuspendidoError` comparten 422 pero se arreglan
distinto (uno lo destraba `habilitarCliente`, el otro no se destraba desde ninguna API),
así que son dos excepciones y no una -- mismo criterio que separa `IdempotencyConflictError`
de `BonificadoLimiteError`/`CsrYaExisteError`/`CredencialYaActivaError` a pesar de
compartir el 409.

Las fallas de **transporte** (timeout, DNS, TLS) no se envuelven: se propagan tal cual las
tira Node. "El servidor respondió que no" y "ni pudimos preguntarle" son dos causas con
remedios distintos.

## Onboarding de una credencial: homologación vs. producción

Cada credencial AFIP (la de un Cliente, no la tuya) tiene su propio ambiente —
homologación (el sandbox de AFIP, comprobantes que no valen) o producción (comprobantes
fiscales reales) — y es un dato de la credencial, no de contra qué `baseUrl` estás
pegando. Un mismo deployment de arca-service puede tener Clientes en los dos ambientes al
mismo tiempo; no hay un host de homologación aparte.

Ni `generarCsr`/`completarCredencial`, ni `importarCredencial`, ni
`crearConexionAfipEmbedToken` te dejan elegir: los tres usan el default que tenga
configurado ESE deployment, y eso lo decide quien lo opera, no vos ni este paquete.
Tampoco hay ningún campo en ninguna respuesta que diga en qué ambiente quedó una
credencial — si te importa saberlo, preguntale a quien te dio el `baseUrl`.

Si necesitás específicamente un Cliente de prueba en homologación contra un deployment
que por default da de alta en producción, eso no es self-serve: pedile a quien opera
arca-service que te lo configure así — es una decisión que se toma al dar de alta la
credencial, no algo que se pueda pedir por acá después.

## Importar una credencial AFIP existente

`importarCredencial` es el único método con criptografía propia: sella la clave privada
AFIP contra la clave pública de arca-service antes de mandarla, así ese texto nunca existe
en claro fuera de los dos extremos, ni siquiera si un proxy loguea el body.

```ts
await client.importarCredencial(externalRef, {
  cuit: '20301234563',
  certPem: fs.readFileSync('afip.crt', 'utf8'),
  keyPem: fs.readFileSync('afip.key', 'utf8'),
  keyPassword: null,
})
```

El algoritmo (RSA-OAEP + AES-256-GCM) es el mismo del SDK de Python, y hay un test que
sella acá y descifra con la implementación Python real para que no puedan separarse sin
que algo se ponga en rojo.

## Qué no está en esta versión

- **Login local / perfil en `~/.config`.** Un consumidor Node en producción pasa las
  credenciales por variables de entorno; el constructor las toma explícitas y nada más.
- **El CLI.** Vive del lado de Python (`arca-service-client`).
- **Cliente async aparte.** No hace falta: todos los métodos ya devuelven promesas.

## Desarrollo

```
npm install
npm test        # incluye los cross-checks contra el SDK de Python
npm run typecheck
npm run build
```

Los tests de cripto, payloads y webhooks corren la implementación de Python al lado de la
de acá y comparan. Necesitan `python3` con `arca-service-client` instalado, y `openssl`
para generar pares descartables.

## Licencia

Proprietary.
