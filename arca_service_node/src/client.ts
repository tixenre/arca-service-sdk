/**
 * `ArcaServiceClient`, envoltorio fino sobre la API HTTP de arca-service. Un método por
 * endpoint -- no una capa de abstracción propia encima, para que cada llamada de acá mapee
 * 1:1 a un endpoint real.
 *
 * **Server-side únicamente.** Este cliente lleva el certificado mTLS y la API key de TU
 * Plataforma: las dos son credenciales de servidor. En Next.js eso significa route handlers,
 * server actions o `getServerSideProps`, nunca un componente de cliente. Si el bundle del
 * browser llega a importar esto, las credenciales se publican.
 *
 * Modelo Cliente/Plataforma: `externalRef` identifica un Cliente (el CUIT/CUIL dueño real
 * de la facturación) -- nunca lo elegís vos, lo devuelve `porCuit()` la primera vez que
 * onboardeás un CUIT.
 */

import { readFileSync } from 'node:fs'
import { Agent } from 'node:https'
import { X509Certificate, createPrivateKey } from 'node:crypto'

import { seal } from './crypto.js'
import { CredentialsInvalidError, raiseForStatus } from './errors.js'
import { request, type HttpResponse, type RequestSpec, type Transporte } from './http.js'
import {
  bonificadoFromJson,
  comprobanteToPayload,
  credencialFromJson,
  diagnosticoFromJson,
  embedTokenFromJson,
  emisionFromJson,
  facturacionFromJson,
  generarCsrFromJson,
  listaComprobantesFromJson,
  loteItemFromJson,
  onboardingFromJson,
  personaArcaFromJson,
  previewFromJson,
  puntosVentaFromJson,
  sesionEmbebidaToPayload,
  type BonificadoResult,
  type ComprobanteInput,
  type ConexionAfipEmbedTokenResult,
  type CredencialResult,
  type DiagnosticoResult,
  type EmbedTokenResult,
  type EmisionResult,
  type FacturacionResult,
  type FechaISO,
  type GenerarCsrResult,
  type ListaComprobantesResult,
  type LoteItemResult,
  type OnboardingResult,
  type PersonaArca,
  type PreviewResult,
  type PuntosVentaResult,
  type SesionEmbebidaInput,
  type SesionEmbebidaResult,
} from './models.js'

const TIMEOUT_MS_DEFAULT = 30_000

/**
 * Los tres layouts que aceptan los doce métodos que renderizan. `"oficial"` es el mismo
 * default que usa el servidor si no se manda `layout`.
 *
 * `"simplificada"` es la única con límites: es una tarjeta chica y NO recorta lo que no
 * entra, devuelve 422 (`RequestError`) si el comprobante tiene más de 3 ítems, o si algún
 * ítem no se puede resumir a "descripción + importe" sin perder nada (descripción de más de
 * 40 caracteres, cantidad != 1, con bonificación, con detalle, o con una unidad de medida
 * distinta de la default). Las otras dos no tienen límite.
 */
export type Layout = 'oficial' | 'detallada' | 'simplificada'

export const LAYOUT_DEFAULT: Layout = 'oficial'

export interface ArcaServiceClientOptions {
  /** Raíz del servicio SIN el prefijo de versión, ej. `"https://arca.mancino.dev"`. */
  baseUrl: string
  /** La API key de tu Plataforma (Bearer token). */
  apiKey: string
  /**
   * El certificado mTLS de tu Plataforma, en PEM. Pasá `clientCert`/`clientKey` con el
   * contenido (lo normal cuando vienen de variables de entorno) o `clientCertPath`/
   * `clientKeyPath` con rutas en disco.
   */
  clientCert?: string | Buffer
  clientKey?: string | Buffer
  clientCertPath?: string
  clientKeyPath?: string
  /** Default 30 000 ms. */
  timeoutMs?: number
}

interface ParDeClaves {
  cert: string | Buffer
  key: string | Buffer
}

function resolverPar(o: ArcaServiceClientOptions): ParDeClaves {
  const cert = o.clientCert ?? (o.clientCertPath ? readFileSync(o.clientCertPath) : undefined)
  const key = o.clientKey ?? (o.clientKeyPath ? readFileSync(o.clientKeyPath) : undefined)
  if (!cert || !key) {
    throw new CredentialsInvalidError(
      'Faltan las credenciales mTLS: pasá clientCert/clientKey (contenido PEM) o ' +
        'clientCertPath/clientKeyPath (rutas).',
    )
  }
  return { cert, key }
}

/**
 * Confirma que el certificado y la clave son un par ANTES del primer request. Sin esto, un
 * par que no corresponde recién falla en el handshake TLS, mucho más lejos de la causa y
 * con un error de OpenSSL sin contexto. Es el mismo problema que aparece copiando
 * credenciales a mano hacia variables de entorno, donde alcanza con que se pierda un
 * carácter.
 */
function validarPar({ cert, key }: ParDeClaves): void {
  let x509: X509Certificate
  try {
    x509 = new X509Certificate(cert)
  } catch (cause) {
    throw new CredentialsInvalidError(`clientCert no es un certificado PEM válido: ${cause}`)
  }
  let privada: ReturnType<typeof createPrivateKey>
  try {
    privada = createPrivateKey(key)
  } catch (cause) {
    throw new CredentialsInvalidError(`clientKey no es una clave privada PEM válida: ${cause}`)
  }
  if (!x509.checkPrivateKey(privada)) {
    throw new CredentialsInvalidError('clientCert y clientKey no forman un par válido.')
  }
}

export class ArcaServiceClient {
  readonly #t: Transporte

  constructor(options: ArcaServiceClientOptions) {
    const par = resolverPar(options)
    validarPar(par)
    this.#t = {
      // `keepAlive` para no rehacer el handshake TLS en cada llamada: un onboarding
      // encadena varios requests seguidos contra el mismo host.
      agent: new Agent({ cert: par.cert, key: par.key, keepAlive: true }),
      apiKey: options.apiKey,
      baseUrl: options.baseUrl,
      timeoutMs: options.timeoutMs ?? TIMEOUT_MS_DEFAULT,
    }
  }

  /** Cierra las conexiones que quedaron abiertas por keep-alive. */
  close(): void {
    this.#t.agent.destroy()
  }

  async #pedir(spec: RequestSpec): Promise<HttpResponse> {
    const res = await request(this.#t, spec)
    raiseForStatus(res.statusCode, res.body.toString('utf8'), res.headers)
    return res
  }

  async #json<T>(spec: RequestSpec): Promise<T> {
    const res = await this.#pedir(spec)
    return JSON.parse(res.body.toString('utf8')) as T
  }

  async #texto(spec: RequestSpec): Promise<string> {
    return (await this.#pedir(spec)).body.toString('utf8')
  }

  async #binario(spec: RequestSpec): Promise<Buffer> {
    return (await this.#pedir(spec)).body
  }

  // ------------------------------------------------------------------
  // Cliente -- onboarding por CUIT + vínculo con tu Plataforma. SIEMPRE el
  // primer llamado: todo lo demás necesita el `externalRef` que devuelve.
  // ------------------------------------------------------------------

  /**
   * Idempotente en dos sentidos: un CUIT nuevo crea el Cliente; uno ya onboardeado por OTRA
   * Plataforma se reusa, creando (o reactivando) sólo TU vínculo con él. Llamalo las veces
   * que haga falta: nunca duplica nada, siempre devuelve el mismo `externalRef`.
   */
  async porCuit(cuit: string): Promise<OnboardingResult> {
    return onboardingFromJson(
      await this.#json({ method: 'POST', path: '/clientes/por-cuit', json: { cuit } }),
    )
  }

  /**
   * Togglea si este Cliente, usado A TRAVÉS de TU Plataforma, queda exento de pagar su
   * propia suscripción -- nunca afecta su vínculo con ninguna otra Plataforma. Activar un
   * vínculo nuevo puede levantar `BonificadoLimiteError` (409) si tu Plataforma llegó al
   * límite configurado; desactivar nunca lo levanta.
   */
  async setBonificado(externalRef: string, bonificado: boolean): Promise<BonificadoResult> {
    return bonificadoFromJson(
      await this.#json({
        method: 'PUT',
        path: `/clientes/${externalRef}/bonificado`,
        json: { bonificado },
      }),
    )
  }

  /**
   * Configura el IIBB/nombre de fantasía de ESTE Cliente para el render de sus comprobantes
   * -- una sola vez, no en cada emisión. Razón social y domicilio no se aceptan acá: los
   * trae el padrón. Los dos parámetros son opcionales; el que no mandes queda como estaba
   * (y vuelve `null` si nunca se configuró).
   */
  async setFacturacion(
    externalRef: string,
    datos: { iibb?: string; nombreComercial?: string } = {},
  ): Promise<FacturacionResult> {
    const json: Record<string, unknown> = {}
    if (datos.iibb !== undefined) json['iibb'] = datos.iibb
    if (datos.nombreComercial !== undefined) json['nombre_comercial'] = datos.nombreComercial
    return facturacionFromJson(
      await this.#json({ method: 'PUT', path: `/clientes/${externalRef}/facturacion`, json }),
    )
  }

  // ------------------------------------------------------------------
  // Onboarding de credencial
  // ------------------------------------------------------------------

  /**
   * Genera el CSR que el Cliente sube al portal de AFIP. Con un CSR pendiente o una
   * credencial activa levanta `CsrYaExisteError`/`CredencialYaActivaError` (409) salvo que
   * pases `regenerar: true`.
   */
  async generarCsr(
    externalRef: string,
    cuit: string,
    opciones: { regenerar?: boolean } = {},
  ): Promise<GenerarCsrResult> {
    return generarCsrFromJson(
      await this.#json({
        method: 'POST',
        path: `/clientes/${externalRef}/csr`,
        json: { cuit, regenerar: opciones.regenerar ?? false },
      }),
    )
  }

  /** Cierra el flujo de `generarCsr` con el certificado que devolvió AFIP. */
  async completarCredencial(
    externalRef: string,
    certPem: string,
    opciones: { pointOfSale?: number } = {},
  ): Promise<CredencialResult> {
    return credencialFromJson(
      await this.#json({
        method: 'POST',
        path: `/clientes/${externalRef}/credencial/completar`,
        json: { cert_pem: certPem, point_of_sale: opciones.pointOfSale ?? 0 },
      }),
    )
  }

  /**
   * Trae un certificado + clave AFIP que YA existen (otro sistema, una migración). Sella
   * `keyPem`/`keyPassword` con la clave pública vigente de arca-service antes de mandarlos:
   * la clave privada AFIP nunca viaja en claro, ni siquiera adentro del TLS. `certPem`/
   * `cuit`/`pointOfSale` no son secretos y viajan tal cual.
   */
  async importarCredencial(
    externalRef: string,
    datos: {
      cuit: string
      certPem: string
      keyPem: string
      keyPassword?: string | null
      pointOfSale?: number
    },
  ): Promise<CredencialResult> {
    const pub = await this.#json<{ public_key_pem: string }>({
      method: 'GET',
      path: '/envelope/clave-publica',
    })

    // Las claves de adentro del sobre van en snake_case: las lee arca-service después de
    // descifrar, no este paquete.
    const secreto = JSON.stringify({
      key_pem: datos.keyPem,
      key_password: datos.keyPassword ?? null,
    })
    const sealed = await seal(Buffer.from(secreto, 'utf8'), pub.public_key_pem)

    return credencialFromJson(
      await this.#json({
        method: 'POST',
        path: `/clientes/${externalRef}/credencial/importar`,
        json: {
          cuit: datos.cuit,
          cert_pem: datos.certPem,
          point_of_sale: datos.pointOfSale ?? 0,
          sealed,
        },
      }),
    )
  }

  /** Chequeos sobre la credencial activa: qué falta para poder emitir. */
  async diagnosticarCredencial(externalRef: string): Promise<DiagnosticoResult> {
    return diagnosticoFromJson(
      await this.#json({ method: 'POST', path: `/clientes/${externalRef}/credencial/diagnostico` }),
    )
  }

  /** Requiere que la credencial ya exista -- si no, `NotFoundError`. */
  async listarPuntosDeVenta(externalRef: string): Promise<PuntosVentaResult> {
    return puntosVentaFromJson(
      await this.#json({ method: 'GET', path: `/clientes/${externalRef}/credencial/puntos-venta` }),
    )
  }

  /**
   * Un link público y de vida corta a un flujo INTERACTIVO donde tu cliente final gestiona
   * su propia conexión AFIP, sin loguearse en arca-service ni ver nada de tu backend.
   */
  async crearConexionAfipEmbedToken(externalRef: string): Promise<ConexionAfipEmbedTokenResult> {
    return embedTokenFromJson(
      await this.#json({
        method: 'POST',
        path: `/clientes/${externalRef}/conexion-afip/embed-token`,
      }),
    )
  }

  /** Consulta el padrón de AFIP para un CUIT, con la credencial de este Cliente. */
  async consultarPadron(externalRef: string, cuit: string): Promise<PersonaArca> {
    return personaArcaFromJson(
      await this.#json({ method: 'GET', path: `/clientes/${externalRef}/padron/${cuit}` }),
    )
  }

  // ------------------------------------------------------------------
  // Preview -- lo que se emitiría, sin emitir nada
  // ------------------------------------------------------------------

  async previewComprobante(
    externalRef: string,
    comprobante: ComprobanteInput,
  ): Promise<PreviewResult> {
    return previewFromJson(
      await this.#json({
        method: 'POST',
        path: `/clientes/${externalRef}/comprobantes/preview`,
        json: comprobanteToPayload(comprobante),
      }),
    )
  }

  async previewNotaCredito(
    externalRef: string,
    notaCredito: ComprobanteInput,
  ): Promise<PreviewResult> {
    return previewFromJson(
      await this.#json({
        method: 'POST',
        path: `/clientes/${externalRef}/notas-credito/preview`,
        json: comprobanteToPayload(notaCredito),
      }),
    )
  }

  async previewNotaDebito(
    externalRef: string,
    notaDebito: ComprobanteInput,
  ): Promise<PreviewResult> {
    return previewFromJson(
      await this.#json({
        method: 'POST',
        path: `/clientes/${externalRef}/notas-debito/preview`,
        json: comprobanteToPayload(notaDebito),
      }),
    )
  }

  // Preview renderizado: el mismo documento que saldría, marcado como vista previa. Ver
  // `Layout` para los límites de "simplificada".

  #previewRender(
    externalRef: string,
    segmento: string,
    formato: string,
    comprobante: ComprobanteInput,
    layout: Layout,
  ): RequestSpec {
    return {
      method: 'POST',
      path: `/clientes/${externalRef}/${segmento}/preview/comprobante.${formato}`,
      json: { ...comprobanteToPayload(comprobante), layout },
    }
  }

  async previewComprobanteHtml(
    externalRef: string,
    comprobante: ComprobanteInput,
    opciones: { layout?: Layout } = {},
  ): Promise<string> {
    return this.#texto(
      this.#previewRender(externalRef, 'comprobantes', 'html', comprobante, opciones.layout ?? LAYOUT_DEFAULT),
    )
  }

  async previewComprobantePdf(
    externalRef: string,
    comprobante: ComprobanteInput,
    opciones: { layout?: Layout } = {},
  ): Promise<Buffer> {
    return this.#binario(
      this.#previewRender(externalRef, 'comprobantes', 'pdf', comprobante, opciones.layout ?? LAYOUT_DEFAULT),
    )
  }

  async previewComprobanteImagen(
    externalRef: string,
    comprobante: ComprobanteInput,
    opciones: { layout?: Layout } = {},
  ): Promise<Buffer> {
    return this.#binario(
      this.#previewRender(externalRef, 'comprobantes', 'imagen', comprobante, opciones.layout ?? LAYOUT_DEFAULT),
    )
  }

  async previewNotaCreditoHtml(
    externalRef: string,
    notaCredito: ComprobanteInput,
    opciones: { layout?: Layout } = {},
  ): Promise<string> {
    return this.#texto(
      this.#previewRender(externalRef, 'notas-credito', 'html', notaCredito, opciones.layout ?? LAYOUT_DEFAULT),
    )
  }

  async previewNotaCreditoPdf(
    externalRef: string,
    notaCredito: ComprobanteInput,
    opciones: { layout?: Layout } = {},
  ): Promise<Buffer> {
    return this.#binario(
      this.#previewRender(externalRef, 'notas-credito', 'pdf', notaCredito, opciones.layout ?? LAYOUT_DEFAULT),
    )
  }

  async previewNotaCreditoImagen(
    externalRef: string,
    notaCredito: ComprobanteInput,
    opciones: { layout?: Layout } = {},
  ): Promise<Buffer> {
    return this.#binario(
      this.#previewRender(externalRef, 'notas-credito', 'imagen', notaCredito, opciones.layout ?? LAYOUT_DEFAULT),
    )
  }

  async previewNotaDebitoHtml(
    externalRef: string,
    notaDebito: ComprobanteInput,
    opciones: { layout?: Layout } = {},
  ): Promise<string> {
    return this.#texto(
      this.#previewRender(externalRef, 'notas-debito', 'html', notaDebito, opciones.layout ?? LAYOUT_DEFAULT),
    )
  }

  async previewNotaDebitoPdf(
    externalRef: string,
    notaDebito: ComprobanteInput,
    opciones: { layout?: Layout } = {},
  ): Promise<Buffer> {
    return this.#binario(
      this.#previewRender(externalRef, 'notas-debito', 'pdf', notaDebito, opciones.layout ?? LAYOUT_DEFAULT),
    )
  }

  async previewNotaDebitoImagen(
    externalRef: string,
    notaDebito: ComprobanteInput,
    opciones: { layout?: Layout } = {},
  ): Promise<Buffer> {
    return this.#binario(
      this.#previewRender(externalRef, 'notas-debito', 'imagen', notaDebito, opciones.layout ?? LAYOUT_DEFAULT),
    )
  }

  // ------------------------------------------------------------------
  // Emisión -- SIEMPRE asincrónica: la respuesta vuelve `pending` y el CAE
  // llega después. Pollear `getComprobante` o esperar el webhook.
  // ------------------------------------------------------------------

  async emitirComprobante(
    externalRef: string,
    comprobante: ComprobanteInput,
  ): Promise<EmisionResult> {
    return emisionFromJson(
      await this.#json({
        method: 'POST',
        path: `/clientes/${externalRef}/comprobantes`,
        json: comprobanteToPayload(comprobante),
      }),
    )
  }

  async emitirNotaCredito(
    externalRef: string,
    notaCredito: ComprobanteInput,
  ): Promise<EmisionResult> {
    return emisionFromJson(
      await this.#json({
        method: 'POST',
        path: `/clientes/${externalRef}/notas-credito`,
        json: comprobanteToPayload(notaCredito),
      }),
    )
  }

  async emitirNotaDebito(
    externalRef: string,
    notaDebito: ComprobanteInput,
  ): Promise<EmisionResult> {
    return emisionFromJson(
      await this.#json({
        method: 'POST',
        path: `/clientes/${externalRef}/notas-debito`,
        json: comprobanteToPayload(notaDebito),
      }),
    )
  }

  /** El estado actual de una emisión. Es lo que hay que pollear hasta que deje de ser `"pending"`. */
  async getComprobante(externalRef: string, idempotencyKey: string): Promise<EmisionResult> {
    return emisionFromJson(
      await this.#json({
        method: 'GET',
        path: `/clientes/${externalRef}/comprobantes/${idempotencyKey}`,
      }),
    )
  }

  /**
   * Todos los comprobantes de este Cliente, más nuevo primero. `creadoDesde`/`creadoHasta`
   * filtran por cuándo se PIDIÓ la emisión, no por la fecha fiscal del comprobante.
   * `receptorCuit` acepta con guiones o pelado, y sólo encuentra lo emitido con CUIT (un
   * receptor por DNI o consumidor final nunca aparece filtrando así). Sin resultados es una
   * lista vacía, nunca un 404.
   */
  async listarComprobantes(
    externalRef: string,
    filtros: {
      estado?: 'pending' | 'issued' | 'error'
      tipo?: 'factura' | 'nota_credito' | 'nota_debito'
      creadoDesde?: FechaISO
      creadoHasta?: FechaISO
      receptorCuit?: string
      limit?: number
      offset?: number
    } = {},
  ): Promise<ListaComprobantesResult> {
    return listaComprobantesFromJson(
      await this.#json({
        method: 'GET',
        path: `/clientes/${externalRef}/comprobantes`,
        query: {
          limit: filtros.limit ?? 50,
          offset: filtros.offset ?? 0,
          estado: filtros.estado,
          tipo: filtros.tipo,
          receptor_cuit: filtros.receptorCuit,
          creado_desde: filtros.creadoDesde,
          creado_hasta: filtros.creadoHasta,
        },
      }),
    )
  }

  // ------------------------------------------------------------------
  // Sesión embebida (iframe) -- puerta de entrada ALTERNATIVA a emitir*.
  //
  // Crear la sesión NO es idempotente aunque la emisión sí lo sea: llamar dos
  // veces con la misma `idempotencyKey` devuelve otro `embedUrl` en vez de un
  // 409 (es lo que hace falta cuando el comprador abandonó y el link venció).
  // De las dos sesiones sale UN solo comprobante.
  // ------------------------------------------------------------------

  async crearSesionEmbebidaComprobante(
    externalRef: string,
    comprobante: SesionEmbebidaInput,
  ): Promise<SesionEmbebidaResult> {
    return embedTokenFromJson(
      await this.#json({
        method: 'POST',
        path: `/clientes/${externalRef}/comprobantes/sesion-embebida`,
        json: sesionEmbebidaToPayload(comprobante),
      }),
    )
  }

  async crearSesionEmbebidaNotaCredito(
    externalRef: string,
    notaCredito: SesionEmbebidaInput,
  ): Promise<SesionEmbebidaResult> {
    return embedTokenFromJson(
      await this.#json({
        method: 'POST',
        path: `/clientes/${externalRef}/notas-credito/sesion-embebida`,
        json: sesionEmbebidaToPayload(notaCredito),
      }),
    )
  }

  async crearSesionEmbebidaNotaDebito(
    externalRef: string,
    notaDebito: SesionEmbebidaInput,
  ): Promise<SesionEmbebidaResult> {
    return embedTokenFromJson(
      await this.#json({
        method: 'POST',
        path: `/clientes/${externalRef}/notas-debito/sesion-embebida`,
        json: sesionEmbebidaToPayload(notaDebito),
      }),
    )
  }

  // ------------------------------------------------------------------
  // Lote -- colapsa N comprobantes en un solo request. SIEMPRE 200 con el
  // resultado de CADA ítem adentro: un ítem con `idempotencyKey` en conflicto
  // no aborta a los demás, así que esto nunca levanta por un ítem puntual (sí
  // por el lote entero: más de 200 ítems, o falta el campo).
  // ------------------------------------------------------------------

  async emitirLoteComprobantes(
    externalRef: string,
    comprobantes: ComprobanteInput[],
  ): Promise<LoteItemResult[]> {
    const items = await this.#json<Record<string, any>[]>({
      method: 'POST',
      path: `/clientes/${externalRef}/comprobantes/lote`,
      json: { comprobantes: comprobantes.map(comprobanteToPayload) },
    })
    return items.map(loteItemFromJson)
  }

  async emitirLoteNotasCredito(
    externalRef: string,
    notasCredito: ComprobanteInput[],
  ): Promise<LoteItemResult[]> {
    const items = await this.#json<Record<string, any>[]>({
      method: 'POST',
      path: `/clientes/${externalRef}/notas-credito/lote`,
      json: { notas_credito: notasCredito.map(comprobanteToPayload) },
    })
    return items.map(loteItemFromJson)
  }

  async emitirLoteNotasDebito(
    externalRef: string,
    notasDebito: ComprobanteInput[],
  ): Promise<LoteItemResult[]> {
    const items = await this.#json<Record<string, any>[]>({
      method: 'POST',
      path: `/clientes/${externalRef}/notas-debito/lote`,
      json: { notas_debito: notasDebito.map(comprobanteToPayload) },
    })
    return items.map(loteItemFromJson)
  }

  /** Reintenta a pedido la entrega del webhook de una emisión. */
  async reenviarWebhook(externalRef: string, idempotencyKey: string): Promise<EmisionResult> {
    return emisionFromJson(
      await this.#json({
        method: 'POST',
        path: `/clientes/${externalRef}/comprobantes/${idempotencyKey}/webhook/reenviar`,
      }),
    )
  }

  // ------------------------------------------------------------------
  // Documento renderizado de una emisión que ya existe
  // ------------------------------------------------------------------

  async getComprobanteHtml(
    externalRef: string,
    idempotencyKey: string,
    opciones: { layout?: Layout } = {},
  ): Promise<string> {
    return this.#texto({
      method: 'GET',
      path: `/clientes/${externalRef}/comprobantes/${idempotencyKey}/comprobante.html`,
      query: { layout: opciones.layout ?? LAYOUT_DEFAULT },
    })
  }

  async getComprobantePdf(
    externalRef: string,
    idempotencyKey: string,
    opciones: { layout?: Layout } = {},
  ): Promise<Buffer> {
    return this.#binario({
      method: 'GET',
      path: `/clientes/${externalRef}/comprobantes/${idempotencyKey}/comprobante.pdf`,
      query: { layout: opciones.layout ?? LAYOUT_DEFAULT },
    })
  }

  async getComprobanteImagen(
    externalRef: string,
    idempotencyKey: string,
    opciones: { layout?: Layout } = {},
  ): Promise<Buffer> {
    return this.#binario({
      method: 'GET',
      path: `/clientes/${externalRef}/comprobantes/${idempotencyKey}/comprobante.imagen`,
      query: { layout: opciones.layout ?? LAYOUT_DEFAULT },
    })
  }

  /**
   * Un link público y de vida corta para mostrarle un comprobante YA emitido a alguien sin
   * que tu backend esté en el medio. Si tu backend ya tiene al usuario logueado, es más
   * simple `getComprobanteHtml`/`Pdf` del lado servidor.
   */
  async crearEmbedToken(externalRef: string, idempotencyKey: string): Promise<EmbedTokenResult> {
    return embedTokenFromJson(
      await this.#json({
        method: 'POST',
        path: `/clientes/${externalRef}/comprobantes/${idempotencyKey}/embed-token`,
      }),
    )
  }
}
