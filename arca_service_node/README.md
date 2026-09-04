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
preview) toman `{ layout }`:

| `layout` | Para qué |
|---|---|
| `'oficial'` (default) | El comprobante completo |
| `'detallada'` | Igual, con más desglose por ítem |
| `'simplificada'` | Una tarjeta chica, para compartir |

`'simplificada'` **rechaza** el comprobante que no le entra en vez de recortarlo: devuelve
422 (`RequestError`) si hay más de 3 ítems, o si algún ítem tiene descripción de más de 40
caracteres, `cantidad` distinta de 1, bonificación, detalle, o una unidad de medida que no
sea la default. Si no entra, pedilo en `'oficial'` o `'detallada'`, que no tienen límite.

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
| `AfipRechazoError` | 422 | `.afip` trae los códigos de AFIP sin masticar |
| `AfipUnavailableError` | 502 | Transitorio, reintentable con backoff |
| `ServicioNoDisponibleError` | 503 | Este request puntual; **no significa que la emisión haya fallado** |

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
