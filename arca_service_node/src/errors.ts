/**
 * Un tipo por `type` del sobre de error de arca-service, más un puñado de subclases con
 * nombre propio para los `code` que vale la pena distinguir sin obligar a mirar `.code` a
 * mano. El sobre real:
 *
 *     {"error": {"type": "afip", "code": "afip_rechazo", "message": "...", "afip": [...]}}
 *
 * `type` son CUATRO y no crecen (`request`/`configuracion`/`afip`/`interno`). `code` es el
 * motivo puntual y SÍ crece con el tiempo, así que un `code` que este módulo no reconoce
 * cae en la subclase de su `type`, nunca en un catch-all sin tipar -- no hace falta
 * enumerar cada `code` acá para que un error nuevo del servidor siga siendo manejable.
 *
 * `ArcaServiceError` es la base común: quien solo necesita `catch` de "algo salió mal" no
 * tiene que conocer cada subtipo; quien sí necesita discriminar puede mirar `.code`
 * (estable, para programas) o `.type` (grueso, para decidir qué hacer) -- nunca
 * `.message`, que está escrito para que lo lea una persona, no para que un programa lo
 * compare.
 */

/**
 * Un ítem de `AfipRechazoError.afip` -- un código de rechazo de AFIP tal cual, sin
 * masticar (`codigo` es el numérico de WSFEv1, ej. `10016`; `mensaje` es el texto que
 * manda AFIP para ese código, no reescrito por arca-service).
 */
export interface AfipErrorDetail {
  codigo: number
  mensaje: string
}

export interface ArcaServiceErrorInit {
  type: string
  code: string
  message: string
  statusCode: number
  param?: string | null
  afip?: readonly AfipErrorDetail[] | null
  /** El body crudo tal cual llegó, por si hace falta para diagnosticar. */
  rawBody?: string
}

/**
 * Base de todo error que arca-service devolvió como respuesta HTTP (status >= 400). NO
 * cubre fallas de transporte (timeout, DNS, conexión rechazada, TLS) -- esas se dejan
 * propagar tal cual las tira Node; envolverlas acá mezclaría "arca-service respondió que
 * no" con "ni siquiera pudimos preguntarle", dos causas con remedios distintos.
 */
export class ArcaServiceError extends Error {
  readonly type: string
  readonly code: string
  readonly statusCode: number
  /** El campo del request al que apunta el error, cuando aplica. */
  readonly param: string | null
  /** Los códigos de rechazo de AFIP sin masticar; sólo poblado en `AfipRechazoError`. */
  readonly afip: readonly AfipErrorDetail[] | null
  readonly rawBody: string | undefined

  constructor(init: ArcaServiceErrorInit) {
    super(init.message)
    this.name = new.target.name
    this.type = init.type
    this.code = init.code
    this.statusCode = init.statusCode
    this.param = init.param ?? null
    this.afip = init.afip ?? null
    this.rawBody = init.rawBody
  }
}

/**
 * `type: "request"` -- el problema está en lo que mandaste. Cambiá el request (según
 * `.code`/`.param`) y reintentá; reintentar sin cambiar nada da el mismo resultado.
 */
export class RequestError extends ArcaServiceError {}

/**
 * `type: "configuracion"` -- nada que este código pueda arreglar: el dueño del CUIT tiene
 * un trámite pendiente del lado de AFIP (portal de AFIP), no un problema de este request
 * puntual. Reintentar el mismo request no cambia nada hasta que se resuelva afuera.
 */
export class ConfiguracionError extends ArcaServiceError {}

/**
 * `type: "afip"` -- la respuesta vino de AFIP, no de arca-service. `.code` dice si
 * reintentar sirve.
 */
export class AfipError extends ArcaServiceError {}

/**
 * `type: "interno"` -- problema del lado de arca-service o de su infraestructura, no tuyo
 * ni del Cliente. Avisale a arca-service si persiste.
 */
export class InternoError extends ArcaServiceError {}

/**
 * 404 -- el recurso pedido no existe para este Cliente, o el `externalRef` mismo no
 * existe / tu Plataforma no está autorizada contra él: deliberadamente el mismo 404
 * genérico para los dos casos, para no filtrarle a un caller no autorizado si un
 * `externalRef` existe o no.
 */
export class NotFoundError extends RequestError {}

/**
 * 401/403 -- arca-service rechazó las credenciales de TU Plataforma. A pesar de heredar
 * de `RequestError` (el `type` que manda el servidor es `"request"`), NO es un problema de
 * lo que mandaste: reintentar el mismo request con el payload corregido da exactamente lo
 * mismo. Lo que hay que revisar es con qué te estás autenticando.
 *
 * Las dos capas de auth fallan con el MISMO error, a propósito -- desde afuera no se puede
 * distinguir cuál de las dos te rechazó. Así que cualquiera de estas lo levanta: la API key
 * no existe, está revocada o vencida; tu Plataforma está desactivada; el certificado mTLS
 * no llegó, no es válido, o el request no entró por donde tiene que entrar.
 */
export class CredentialsRejectedError extends RequestError {}

/**
 * 409 -- ya existe un intento con esa `idempotencyKey` pero con datos DISTINTOS. Elegí una
 * key nueva si es una emisión genuinamente distinta; si es un reintento de la MISMA emisión
 * con el MISMO payload, esto no debería pasar -- construila determinística a partir de algo
 * estable de tu propio dominio (ej. el id de la orden), nunca de un valor random.
 */
export class IdempotencyConflictError extends RequestError {}

/**
 * 409 -- `setBonificado(externalRef, true)` chocó contra el circuit-breaker de seguridad de
 * tu Plataforma (0 por default, fail-closed). NO es un error tuyo ni del Cliente: pedile a
 * arca-service que revise/suba el límite. Desactivar nunca choca contra esto.
 */
export class BonificadoLimiteError extends ConfiguracionError {}

/**
 * 409 -- `generarCsr` chocó con un CSR que ya se había generado antes para este Cliente y
 * todavía no se completó con un certificado. Pasá `regenerar: true` para descartarlo.
 */
export class CsrYaExisteError extends RequestError {}

/**
 * 409 -- `generarCsr` chocó con una credencial que ya está activa para este Cliente. Pasá
 * `regenerar: true` si el objetivo es reemplazarla.
 */
export class CredencialYaActivaError extends RequestError {}

/**
 * 429 -- se excedió el límite de requests por Plataforma. `retryAfter`: segundos a esperar
 * antes de reintentar (header `Retry-After`), `null` si el servidor no lo mandó.
 */
export class RateLimitedError extends RequestError {
  readonly retryAfter: number | null

  constructor(init: ArcaServiceErrorInit & { retryAfter?: number | null }) {
    super(init)
    this.retryAfter = init.retryAfter ?? null
  }
}

/**
 * 422 -- el punto de venta desde el que se iba a emitir no está habilitado en AFIP. Se
 * arregla en el portal de AFIP, nunca cambiando el request.
 */
export class PuntoVentaNoHabilitadoError extends ConfiguracionError {}

/**
 * 422, `param === "comprobante_asociado"` -- la nota de crédito/débito acredita más de lo
 * que queda disponible en la factura que referencia. Lo rechaza arca-service, AFIP nunca lo
 * vio.
 */
export class NotaExcedeComprobanteError extends RequestError {}

/**
 * 422 -- AFIP rechazó el comprobante. NO reintentable sin cambiar algo. `.afip` trae los
 * códigos tal cual los mandó AFIP, para programar contra el número.
 */
export class AfipRechazoError extends AfipError {}

/**
 * 502 -- AFIP no contestó o contestó en un formato inesperado. A diferencia de
 * `AfipRechazoError`, esto SÍ es transitorio: reintentar más tarde (con backoff) puede andar.
 */
export class AfipUnavailableError extends AfipError {}

/**
 * 503 -- arca-service no puede completar ESTE request puntual aunque el servicio esté
 * arriba. **No significa que la emisión haya fallado**: si ya hay CAE sigue estando.
 * Reintentable.
 */
export class ServicioNoDisponibleError extends InternoError {}

/**
 * `clientCert`/`clientKey` no forman un par válido, o alguno no es un PEM parseable. NO es
 * un `ArcaServiceError`: pasa ANTES de que exista ningún request, nunca es una respuesta de
 * arca-service.
 */
export class CredentialsInvalidError extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'CredentialsInvalidError'
  }
}

/**
 * La clave pública recibida de arca-service no es válida -- PEM mal formada, o no es RSA.
 * Nunca una excepción cruda de WebCrypto.
 */
export class EnvelopeError extends Error {
  constructor(message: string, options?: { cause?: unknown }) {
    super(message, options)
    this.name = 'EnvelopeError'
  }
}

type ArcaServiceErrorCtor = new (init: ArcaServiceErrorInit) => ArcaServiceError

/**
 * `code` -> excepción, solo para los que vale la pena distinguir sin obligar a mirar
 * `.code` a mano. Deliberadamente NO exhaustiva: la lista de `code` crece, los cuatro
 * `type` no. Un `code` que no está acá cae en la excepción de su `type`.
 */
const POR_CODE: Record<string, ArcaServiceErrorCtor> = {
  no_encontrado: NotFoundError,
  // Los dos rechazos de credencial: 401 (no te pudimos identificar) y 403 (el request no
  // entró por donde tiene que entrar). Comparten excepción porque comparten remedio.
  no_autenticado: CredentialsRejectedError,
  origen_no_verificado: CredentialsRejectedError,
  idempotency_key_reusada: IdempotencyConflictError,
  csr_ya_existe: CsrYaExisteError,
  credencial_ya_activa: CredencialYaActivaError,
  limite_bonificados_alcanzado: BonificadoLimiteError,
  rate_limit: RateLimitedError as ArcaServiceErrorCtor,
  punto_venta_no_habilitado: PuntoVentaNoHabilitadoError,
  nota_excede_comprobante: NotaExcedeComprobanteError,
  afip_rechazo: AfipRechazoError,
  afip_sin_respuesta: AfipUnavailableError,
  afip_respuesta_ilegible: AfipUnavailableError,
  servicio_no_disponible: ServicioNoDisponibleError,
}

/**
 * `type` -> excepción genérica, para cualquier `code` que `POR_CODE` no reconozca. Estos
 * CUATRO son la única parte del mapeo que arca-service garantiza estable -- por eso el
 * fallback, si ni el `type` viniera reconocible (respuesta corrupta), es `InternoError` y
 * no algo sin tipar.
 */
const POR_TYPE: Record<string, ArcaServiceErrorCtor> = {
  request: RequestError,
  configuracion: ConfiguracionError,
  afip: AfipError,
  interno: InternoError,
}

interface Envelope {
  type: string
  code: string
  message: string
  param: string | null
  afip: AfipErrorDetail[] | null
}

/**
 * Lee `{"error": {...}}` -- el sobre único de toda la API. Si el body no tiene esa forma
 * (un proxy intermedio devolviendo texto/HTML, por ejemplo), no rompe acá: cae a
 * `type="interno"`/`code=""` con el texto crudo en `message`, en vez de un error de parseo
 * que ocultaría el error real detrás de OTRO error.
 */
function parseEnvelope(body: string): Envelope {
  try {
    const error = (JSON.parse(body) as { error?: Record<string, unknown> }).error
    if (!error || typeof error !== 'object') throw new Error('sin sobre')
    const afipRaw = error['afip']
    return {
      type: String(error['type']),
      code: String(error['code']),
      message: String(error['message']),
      param: error['param'] == null ? null : String(error['param']),
      afip: Array.isArray(afipRaw)
        ? afipRaw.map((a: { codigo: number; mensaje: string }) => ({
            codigo: a.codigo,
            mensaje: a.mensaje,
          }))
        : null,
    }
  } catch {
    return { type: 'interno', code: '', message: body, param: null, afip: null }
  }
}

/**
 * Traduce una respuesta con status >= 400 al error tipado que le corresponde, ramificando
 * por `code`/`type` -- nunca por `statusCode` (varios `code` bien distintos comparten
 * status: `idempotency_key_reusada`, `csr_ya_existe` y `limite_bonificados_alcanzado` son
 * los tres 409). No hace nada si el status es < 400.
 */
export function raiseForStatus(
  statusCode: number,
  body: string,
  headers: Record<string, string | string[] | undefined> = {},
): void {
  if (statusCode < 400) return

  const envelope = parseEnvelope(body)
  const Ctor = POR_CODE[envelope.code] ?? POR_TYPE[envelope.type] ?? InternoError

  const init: ArcaServiceErrorInit = {
    type: envelope.type,
    code: envelope.code,
    message: envelope.message,
    statusCode,
    param: envelope.param,
    afip: envelope.afip,
    rawBody: body,
  }

  if (Ctor === (RateLimitedError as ArcaServiceErrorCtor)) {
    const raw = headers['retry-after']
    const retryAfter = Array.isArray(raw) ? raw[0] : raw
    const parsed = retryAfter === undefined ? NaN : Number.parseInt(retryAfter, 10)
    throw new RateLimitedError({ ...init, retryAfter: Number.isNaN(parsed) ? null : parsed })
  }
  throw new Ctor(init)
}
