/**
 * Los modelos de request/response, espejo del contrato JSON real de la API.
 *
 * DOS convenciones que conviene tener presentes, las dos deliberadas:
 *
 * 1. **camelCase de este lado, snake_case en el cable.** Las interfaces de acá usan
 *    `idempotencyKey`/`precioUnitario`, que es lo que espera quien escribe TypeScript; el
 *    servidor habla `idempotency_key`/`precio_unitario`. La traducción NO es automática ni
 *    genérica: cada campo se mapea a mano en `toPayload`/`fromJson`. Es más código, y es a
 *    propósito -- un mapeo explícito falla al compilar cuando el contrato se mueve, y uno
 *    genérico (`snakeCase(key)`) falla en silencio y manda un campo que el servidor ignora.
 *
 * 2. **La plata viaja como `string`, nunca como `number`.** `1000.10` no es representable
 *    en binario y `0.1 + 0.2 !== 0.3`; en un importe fiscal eso es un error que se
 *    descubre tarde. Los importes entran como string (`"1000.00"`) y salen como string,
 *    igual que en el JSON. Si necesitás hacer cuentas, usá una librería de decimales y
 *    convertí en el borde.
 *
 * Por lo mismo, las fechas de comprobante (`fecha`, `fechaServDesde`, ...) son strings
 * `"YYYY-MM-DD"` y no `Date`: un `Date` es un instante, y convertirlo a día calendario
 * reintroduce el bug de zona horaria que se quiere evitar (en un proceso en UTC,
 * `new Date()` ya es "mañana" desde las 21 hora argentina). `expiresAt`, que sí es un
 * instante real, se parsea a `Date`.
 */

import type { AfipErrorDetail } from './errors.js'

/** Un importe: string decimal, ej. `"1000.00"`. Ver la nota de arriba sobre por qué. */
export type Importe = string
/** Un día calendario, `"YYYY-MM-DD"`. */
export type FechaISO = string

// ---------------------------------------------------------------------------
// Request
// ---------------------------------------------------------------------------

/**
 * Un tributo/percepción (Impuestos Internos, percepciones de IIBB, etc.).
 * `codigo`: código de la tabla AFIP `FEParamGetTiposTributos`.
 */
export interface Tributo {
  codigo: number
  baseImponible: Importe
  alicuotaPct: Importe
  importe: Importe
  descripcion?: string
}

/**
 * Un dato opcional del comprobante (ej. CBU/Alias de una FCE MiPyme).
 * `codigo`: código de la tabla AFIP `FEParamGetTiposOpcional`.
 */
export interface Opcional {
  codigo: string
  valor: string
}

/**
 * Un renglón: qué se vendió, a cuánto, y cómo lo trata el IVA. Es la ÚNICA fuente de los
 * importes del comprobante -- no hay un neto aparte para reconciliar.
 *
 * Va `precioUnitario` (sin IVA) o `precioFinal` (con IVA incluido), nunca los dos: no son
 * equivalentes bajo redondeo, así que ninguno se puede derivar del otro.
 *
 * `iva` es el porcentaje en string (`"21"`, `"10.5"`, `"0"`), nunca un id de alícuota -- o
 * `"exento"`/`"noGravado"` para los dos casos que AFIP trata distinto de un 0% discriminado.
 */
export interface ItemFactura {
  descripcion: string
  iva: string
  precioUnitario?: Importe
  precioFinal?: Importe
  codigo?: string
  cantidad?: Importe
  unidadMedida?: string
  bonificacionPct?: Importe
  detalle?: string
}

/**
 * Referencia a la factura original -- obligatoria en una nota de crédito/débito. Con
 * `tipo`/`puntoVenta`/`numero` alcanza si el comprobante original lo emitió este mismo
 * servicio. `cae`/`importeTotal` son para asociar una nota a un comprobante que NO emitió
 * este servicio: van los dos juntos o ninguno.
 */
export interface ComprobanteAsociado {
  tipo: number
  puntoVenta: number
  numero: number
  cuit?: string
  fecha?: FechaISO
  cae?: string
  importeTotal?: Importe
}

/**
 * A quién se le factura. Exactamente una de estas tres formas lo identifica -- el servidor
 * rechaza si no viene ninguna, o si viene más de una:
 *
 *     { cuit: '30712345671' }
 *     { dni: '20111222', nombre: 'Juan Pérez' }
 *     { consumidorFinal: true }
 *
 * Con `cuit`, `nombre`/`domicilio` no hacen falta (y es un 422 si se mandan): los resuelve
 * el padrón de AFIP. Con `dni` sí se aceptan.
 *
 * `email` va con cualquiera de las tres formas. Ojo: hoy queda guardado con el comprobante
 * pero NO se manda ninguna copia a esa casilla -- el envío por email todavía no está activo
 * del lado de arca-service.
 */
export interface Receptor {
  cuit?: string
  dni?: string
  consumidorFinal?: boolean
  nombre?: string
  domicilio?: string
  condicionIva?: number
  email?: string
}

/**
 * El body de `previewComprobante`/`emitirComprobante`, y con `comprobanteAsociado` para las
 * notas de crédito/débito.
 *
 * `fecha`/`puntoVenta`/`moneda` son opcionales. **No mandes `fecha` salvo que necesites una
 * distinta a hoy**: el servidor la calcula con el calendario argentino, y armarla desde un
 * proceso en UTC ya da "mañana" a partir de las 21 hora argentina -- lo que además cambia
 * el payload y convierte un reintento con la misma `idempotencyKey` en un 409.
 */
export interface ComprobanteInput {
  idempotencyKey: string
  concepto: number
  receptor: Receptor
  items?: ItemFactura[]
  puntoVenta?: number
  fecha?: FechaISO
  fechaServDesde?: FechaISO
  fechaServHasta?: FechaISO
  fechaVtoPago?: FechaISO
  moneda?: string
  forzarCbteTipo?: number
  condicionVenta?: string
  tributos?: Tributo[]
  opcionales?: Opcional[]
  comprobanteAsociado?: ComprobanteAsociado
}

/**
 * Mismo body que `ComprobanteInput` pero con `receptor` OPCIONAL, para las sesiones
 * embebidas. Dos modos según lo pases o no:
 *
 * - SIN `receptor`: tu Plataforma sabe cuánto facturar pero no a quién; el comprador
 *   completa su propio dato fiscal dentro del `<iframe>`.
 * - CON `receptor`: tu Plataforma ya lo tiene; el `<iframe>` pasa a ser sólo la pantalla
 *   donde el comprador mira la factura que está por salir y confirma.
 *
 * En los dos casos el resto del payload queda fijo desde este llamado: la página embebida
 * no lo puede cambiar.
 */
export interface SesionEmbebidaInput extends Omit<ComprobanteInput, 'receptor'> {
  receptor?: Receptor
}

function tributoToPayload(t: Tributo): Record<string, unknown> {
  return {
    codigo: t.codigo,
    base_imponible: t.baseImponible,
    alicuota_pct: t.alicuotaPct,
    importe: t.importe,
    descripcion: t.descripcion ?? '',
  }
}

function opcionalToPayload(o: Opcional): Record<string, unknown> {
  return { codigo: o.codigo, valor: o.valor }
}

function itemToPayload(i: ItemFactura): Record<string, unknown> {
  const d: Record<string, unknown> = {
    descripcion: i.descripcion,
    iva: i.iva,
    codigo: i.codigo ?? '',
    cantidad: i.cantidad ?? '1',
    unidad_medida: i.unidadMedida ?? 'unidad',
    bonificacion_pct: i.bonificacionPct ?? '0',
    detalle: i.detalle ?? '',
  }
  if (i.precioUnitario !== undefined) d['precio_unitario'] = i.precioUnitario
  if (i.precioFinal !== undefined) d['precio_final'] = i.precioFinal
  return d
}

function asociadoToPayload(a: ComprobanteAsociado): Record<string, unknown> {
  const d: Record<string, unknown> = {
    tipo: a.tipo,
    punto_venta: a.puntoVenta,
    numero: a.numero,
  }
  if (a.cuit !== undefined) d['cuit'] = a.cuit
  if (a.fecha !== undefined) d['fecha'] = a.fecha
  if (a.cae !== undefined) d['cae'] = a.cae
  if (a.importeTotal !== undefined) d['importe_total'] = a.importeTotal
  return d
}

export function receptorToPayload(r: Receptor): Record<string, unknown> {
  const d: Record<string, unknown> = {
    consumidor_final: r.consumidorFinal ?? false,
    nombre: r.nombre ?? '',
    domicilio: r.domicilio ?? '',
  }
  if (r.cuit !== undefined) d['cuit'] = r.cuit
  if (r.dni !== undefined) d['dni'] = r.dni
  if (r.condicionIva !== undefined) d['condicion_iva'] = r.condicionIva
  if (r.email !== undefined) d['email'] = r.email
  return d
}

/** El body común a `ComprobanteInput` y `SesionEmbebidaInput`, sin el receptor. */
function baseToPayload(c: Omit<ComprobanteInput, 'receptor'>): Record<string, unknown> {
  const d: Record<string, unknown> = {
    idempotency_key: c.idempotencyKey,
    concepto: c.concepto,
    items: (c.items ?? []).map(itemToPayload),
    moneda: c.moneda ?? 'PES',
    condicion_venta: c.condicionVenta ?? 'Contado',
    tributos: (c.tributos ?? []).map(tributoToPayload),
    opcionales: (c.opcionales ?? []).map(opcionalToPayload),
  }
  if (c.puntoVenta !== undefined) d['punto_venta'] = c.puntoVenta
  if (c.fecha !== undefined) d['fecha'] = c.fecha
  if (c.fechaServDesde !== undefined) d['fecha_serv_desde'] = c.fechaServDesde
  if (c.fechaServHasta !== undefined) d['fecha_serv_hasta'] = c.fechaServHasta
  if (c.fechaVtoPago !== undefined) d['fecha_vto_pago'] = c.fechaVtoPago
  if (c.forzarCbteTipo !== undefined) d['forzar_cbte_tipo'] = c.forzarCbteTipo
  if (c.comprobanteAsociado !== undefined) {
    d['comprobante_asociado'] = asociadoToPayload(c.comprobanteAsociado)
  }
  return d
}

export function comprobanteToPayload(c: ComprobanteInput): Record<string, unknown> {
  return { ...baseToPayload(c), receptor: receptorToPayload(c.receptor) }
}

export function sesionEmbebidaToPayload(s: SesionEmbebidaInput): Record<string, unknown> {
  const d = baseToPayload(s)
  if (s.receptor !== undefined) d['receptor'] = receptorToPayload(s.receptor)
  return d
}

// ---------------------------------------------------------------------------
// Response
// ---------------------------------------------------------------------------

type Json = Record<string, any>

/**
 * `.replace('Z', '+00:00')` no hace falta acá: `new Date(...)` de JS entiende el sufijo
 * `Z` nativo, a diferencia del `fromisoformat` de Python en versiones viejas.
 */
function fechaHora(v: string): Date {
  return new Date(v)
}

/**
 * El `externalRef` a usar en TODO lo demás. Nunca lo elijas vos ni lo derives del CUIT: es
 * un UUID que arca-service asigna, estable para ese CUIT sin importar cuántas Plataformas
 * distintas lo hayan onboardeado.
 */
export interface OnboardingResult {
  externalRef: string
}

export interface BonificadoResult {
  bonificado: boolean
}

/**
 * El `iibb`/`nombreComercial` YA guardado. Cualquiera de los dos puede venir en `null`: un
 * update parcial no le pone valor por default al que quedó afuera.
 */
export interface FacturacionResult {
  iibb: string | null
  nombreComercial: string | null
}

/**
 * `embedUrl` es un link PÚBLICO (nadie necesita mTLS ni tu API key para abrirlo) que vale
 * hasta `expiresAt`. Tratalo como un secreto de vida corta: no lo loguees ni lo guardes más
 * tiempo del que dure.
 */
export interface EmbedTokenResult {
  embedUrl: string
  expiresAt: Date
}

export type ConexionAfipEmbedTokenResult = EmbedTokenResult
export type SesionEmbebidaResult = EmbedTokenResult

export interface GenerarCsrResult {
  csrPem: string
  alias: string
}

export interface CredencialResult {
  pointOfSale: number
  active: boolean
}

export interface Chequeo {
  check: string
  ok: boolean
  bloqueante: boolean
  mensaje: string
}

export interface DiagnosticoResult {
  chequeos: Chequeo[]
  listo: boolean
}

export interface PuntoVentaHabilitado {
  nro: number
  emisionTipo: string | null
}

export interface PuntoVentaExcluido {
  nro: number
  motivo: string
  rawEmisionTipo: string | null
}

export interface PuntosVentaResult {
  habilitados: PuntoVentaHabilitado[]
  excluidos: PuntoVentaExcluido[]
}

export interface Actividad {
  idActividad: number
  descripcion: string
  periodo: number
  orden: number
  nomenclador: number
}

export interface Impuesto {
  idImpuesto: number
  descripcion: string
  estado: string
  periodo: number
  motivo: string
}

export interface Domicilio {
  direccion: string
  localidad: string
  provincia: string
  idProvincia: number
  codigoPostal: string
  tipoDomicilio: string
  datoAdicional: string
  tipoDatoAdicional: string
}

export interface Dependencia {
  idDependencia: number
  descripcion: string
  direccion: string
  localidad: string
  provincia: string
  idProvincia: number
  codigoPostal: string
}

export interface Categoria {
  descripcion: string
  idCategoria: number
  idImpuesto: number
  periodo: number
}

export interface Regimen {
  idRegimen: number
  descripcion: string
  tipo: string
  idImpuesto: number
  periodo: number
}

export interface Caracterizacion {
  idCaracterizacion: number
  descripcion: string
  periodo: number
  fechaSolicitud: number
}

export interface ComponenteSociedad {
  idPersonaAsociada: number
  nombre: string
  apellido: string
  razonSocial: string
  tipoComponente: string
  fechaRelacion: Date | null
  fechaVencimiento: Date | null
}

/** Respuesta de `consultarPadron`. */
export interface PersonaArca {
  cuit: string
  razonSocial: string
  nombre: string
  apellido: string
  domicilio: string
  condicionIva: string
  estadoClave: string
  tipoPersona: string
  categoriaMonotributo: string
  actividades: Actividad[]
  impuestos: Impuesto[]
  tipoClave: string
  mesCierre: number
  esSucesion: string
  fechaContratoSocial: Date | null
  fechaFallecimiento: Date | null
  domicilioFiscal: Domicilio | null
  dependencia: Dependencia | null
  caracterizaciones: Caracterizacion[]
  categoriaMonotributoDetalle: Categoria | null
  categoriaAutonomo: Categoria | null
  regimenes: Regimen[]
  componentesSociedad: ComponenteSociedad[]
}

/**
 * Un código cerrado de AFIP con su nombre al lado. `descripcion` es `null` para un código
 * que la tabla no reconoce (una fila vieja con un código que AFIP retiró después), nunca un
 * error: un comprobante ya emitido no puede dejar de poder consultarse porque cambiaron las
 * tablas.
 */
export interface CodigoAfip {
  codigo: number
  descripcion: string | null
}

/**
 * La condición frente al IVA del receptor, con quién la decidió. `fuente`: `"padron"` en el
 * caso normal, `"declarada"` si el padrón no pudo clasificar ese CUIT.
 */
export interface CondicionIvaReceptor {
  codigo: number
  descripcion: string | null
  fuente: string | null
}

/**
 * Qué comprobante es. `letra`/`codigoAfip` son `null` mientras una emisión está `pending`;
 * `puntoVenta`/`numero` no existen en un preview y `numero` recién aparece con el CAE.
 */
export interface ComprobanteInfo {
  tipo: string
  letra: string | null
  codigoAfip: number | null
  puntoVenta: number | null
  numero: number | null
  fecha: FechaISO | null
}

/**
 * Los importes, la moneda y la cotización. Todos strings decimales, ver la nota del
 * encabezado. `moneda`/`cotizacion` son `null` en un preview.
 */
export interface Importes {
  neto: Importe
  iva: Importe
  noGravado: Importe
  exento: Importe
  tributos: Importe
  total: Importe
  moneda: string | null
  cotizacion: Importe | null
}

/** A quién se le facturó -- sólo en una emisión, un preview no confirma receptor todavía. */
export interface ReceptorInfo {
  docTipo: CodigoAfip | null
  docNro: number | null
  nombre: string
  domicilio: string
  condicionIva: CondicionIvaReceptor | null
}

export interface PreviewResult {
  comprobante: ComprobanteInfo
  importes: Importes
}

/**
 * `estado`: `"pending"` recién creada, `"issued"` con `comprobante.numero`/`cae`/
 * `caeVencimiento`/`qrUrl` completos, o `"error"` con `errores` poblado. Mirá SIEMPRE
 * `estado`: `importes` se calcula desde que la emisión se crea, así que no hay ningún
 * importe en cero mientras está `pending` que sirva de proxy.
 *
 * `observaciones`: comentarios de AFIP sobre un comprobante que SÍ autorizó. No bloquean
 * nada ni cambian `estado`, pero vale la pena mostrarlos en vez de descartarlos.
 */
export interface EmisionResult {
  id: string
  idempotencyKey: string
  estado: string
  comprobante: ComprobanteInfo
  importes: Importes
  receptor: ReceptorInfo
  cae: string
  caeVencimiento: FechaISO | null
  qrUrl: string
  errores: AfipErrorDetail[] | null
  observaciones: string[] | null
  webhookDelivered: boolean | null
  webhookLastError: string
}

/**
 * `count` es el total que matchea los filtros (para paginar con `limit`/`offset`), no
 * `items.length` -- son iguales sólo cuando todo entra en una página.
 */
export interface ListaComprobantesResult {
  items: EmisionResult[]
  count: number
}

/**
 * Resultado de UN ítem del lote -- fallo parcial, no todo-o-nada: un ítem con
 * `idempotencyKey` en conflicto no aborta a los demás. `ok: false` trae `error`/`statusCode`
 * poblados en vez de `emision`.
 */
export interface LoteItemResult {
  idempotencyKey: string
  ok: boolean
  emision: EmisionResult | null
  error: string | null
  statusCode: number | null
}

export function onboardingFromJson(d: Json): OnboardingResult {
  return { externalRef: d['external_ref'] }
}

export function bonificadoFromJson(d: Json): BonificadoResult {
  return { bonificado: d['bonificado'] }
}

export function facturacionFromJson(d: Json): FacturacionResult {
  return { iibb: d['iibb'] ?? null, nombreComercial: d['nombre_comercial'] ?? null }
}

export function embedTokenFromJson(d: Json): EmbedTokenResult {
  return { embedUrl: d['embed_url'], expiresAt: fechaHora(d['expires_at']) }
}

export function generarCsrFromJson(d: Json): GenerarCsrResult {
  return { csrPem: d['csr_pem'], alias: d['alias'] }
}

export function credencialFromJson(d: Json): CredencialResult {
  return { pointOfSale: d['point_of_sale'], active: d['active'] }
}

export function diagnosticoFromJson(d: Json): DiagnosticoResult {
  return {
    chequeos: (d['chequeos'] as Json[]).map((c) => ({
      check: c['check'],
      ok: c['ok'],
      bloqueante: c['bloqueante'],
      mensaje: c['mensaje'],
    })),
    listo: d['listo'],
  }
}

export function puntosVentaFromJson(d: Json): PuntosVentaResult {
  return {
    habilitados: (d['habilitados'] as Json[]).map((h) => ({
      nro: h['nro'],
      emisionTipo: h['emision_tipo'] ?? null,
    })),
    excluidos: (d['excluidos'] as Json[]).map((e) => ({
      nro: e['nro'],
      motivo: e['motivo'],
      rawEmisionTipo: e['raw_emision_tipo'] ?? null,
    })),
  }
}

function domicilioFromJson(d: Json | null | undefined): Domicilio | null {
  if (!d) return null
  return {
    direccion: d['direccion'],
    localidad: d['localidad'],
    provincia: d['provincia'],
    idProvincia: d['id_provincia'],
    codigoPostal: d['codigo_postal'],
    tipoDomicilio: d['tipo_domicilio'],
    datoAdicional: d['dato_adicional'] ?? '',
    tipoDatoAdicional: d['tipo_dato_adicional'] ?? '',
  }
}

function dependenciaFromJson(d: Json | null | undefined): Dependencia | null {
  if (!d) return null
  return {
    idDependencia: d['id_dependencia'],
    descripcion: d['descripcion'],
    direccion: d['direccion'],
    localidad: d['localidad'],
    provincia: d['provincia'],
    idProvincia: d['id_provincia'],
    codigoPostal: d['codigo_postal'],
  }
}

function categoriaFromJson(d: Json | null | undefined): Categoria | null {
  if (!d) return null
  return {
    descripcion: d['descripcion'],
    idCategoria: d['id_categoria'],
    idImpuesto: d['id_impuesto'],
    periodo: d['periodo'],
  }
}

function fechaHoraOpcional(v: string | null | undefined): Date | null {
  return v ? fechaHora(v) : null
}

export function personaArcaFromJson(d: Json): PersonaArca {
  return {
    cuit: d['cuit'],
    razonSocial: d['razon_social'],
    nombre: d['nombre'],
    apellido: d['apellido'],
    domicilio: d['domicilio'],
    condicionIva: d['condicion_iva'],
    estadoClave: d['estado_clave'],
    tipoPersona: d['tipo_persona'] ?? '',
    categoriaMonotributo: d['categoria_monotributo'] ?? '',
    actividades: ((d['actividades'] ?? []) as Json[]).map((a) => ({
      idActividad: a['id_actividad'],
      descripcion: a['descripcion'],
      periodo: a['periodo'],
      orden: a['orden'],
      nomenclador: a['nomenclador'] ?? 0,
    })),
    impuestos: ((d['impuestos'] ?? []) as Json[]).map((i) => ({
      idImpuesto: i['id_impuesto'],
      descripcion: i['descripcion'],
      estado: i['estado'],
      periodo: i['periodo'],
      motivo: i['motivo'] ?? '',
    })),
    tipoClave: d['tipo_clave'] ?? '',
    mesCierre: d['mes_cierre'] ?? 0,
    esSucesion: d['es_sucesion'] ?? '',
    fechaContratoSocial: fechaHoraOpcional(d['fecha_contrato_social']),
    fechaFallecimiento: fechaHoraOpcional(d['fecha_fallecimiento']),
    domicilioFiscal: domicilioFromJson(d['domicilio_fiscal']),
    dependencia: dependenciaFromJson(d['dependencia']),
    caracterizaciones: ((d['caracterizaciones'] ?? []) as Json[]).map((c) => ({
      idCaracterizacion: c['id_caracterizacion'],
      descripcion: c['descripcion'],
      periodo: c['periodo'],
      fechaSolicitud: c['fecha_solicitud'] ?? 0,
    })),
    categoriaMonotributoDetalle: categoriaFromJson(d['categoria_monotributo_detalle']),
    categoriaAutonomo: categoriaFromJson(d['categoria_autonomo']),
    regimenes: ((d['regimenes'] ?? []) as Json[]).map((r) => ({
      idRegimen: r['id_regimen'],
      descripcion: r['descripcion'],
      tipo: r['tipo'],
      idImpuesto: r['id_impuesto'],
      periodo: r['periodo'],
    })),
    componentesSociedad: ((d['componentes_sociedad'] ?? []) as Json[]).map((c) => ({
      idPersonaAsociada: c['id_persona_asociada'],
      nombre: c['nombre'],
      apellido: c['apellido'],
      razonSocial: c['razon_social'],
      tipoComponente: c['tipo_componente'],
      fechaRelacion: fechaHoraOpcional(c['fecha_relacion']),
      fechaVencimiento: fechaHoraOpcional(c['fecha_vencimiento']),
    })),
  }
}

function comprobanteInfoFromJson(d: Json): ComprobanteInfo {
  return {
    tipo: d['tipo'],
    letra: d['letra'] ?? null,
    codigoAfip: d['codigo_afip'] ?? null,
    puntoVenta: d['punto_venta'] ?? null,
    numero: d['numero'] ?? null,
    fecha: d['fecha'] ?? null,
  }
}

function importesFromJson(d: Json): Importes {
  return {
    neto: d['neto'],
    iva: d['iva'],
    noGravado: d['no_gravado'],
    exento: d['exento'],
    tributos: d['tributos'],
    total: d['total'],
    moneda: d['moneda'] ?? null,
    cotizacion: d['cotizacion'] ?? null,
  }
}

function receptorInfoFromJson(d: Json): ReceptorInfo {
  const docTipo = d['doc_tipo']
  const condicionIva = d['condicion_iva']
  return {
    docTipo: docTipo ? { codigo: docTipo['codigo'], descripcion: docTipo['descripcion'] ?? null } : null,
    docNro: d['doc_nro'] ?? null,
    nombre: d['nombre'] ?? '',
    domicilio: d['domicilio'] ?? '',
    condicionIva: condicionIva
      ? {
          codigo: condicionIva['codigo'],
          descripcion: condicionIva['descripcion'] ?? null,
          fuente: condicionIva['fuente'] ?? null,
        }
      : null,
  }
}

export function previewFromJson(d: Json): PreviewResult {
  return {
    comprobante: comprobanteInfoFromJson(d['comprobante']),
    importes: importesFromJson(d['importes']),
  }
}

export function emisionFromJson(d: Json): EmisionResult {
  const errores = d['errores']
  return {
    id: d['id'],
    idempotencyKey: d['idempotency_key'],
    estado: d['estado'],
    comprobante: comprobanteInfoFromJson(d['comprobante']),
    importes: importesFromJson(d['importes']),
    receptor: receptorInfoFromJson(d['receptor']),
    cae: d['cae'] ?? '',
    caeVencimiento: d['cae_vencimiento'] ?? null,
    qrUrl: d['qr_url'] ?? '',
    errores:
      errores == null
        ? null
        : (errores as Json[]).map((e) => ({ codigo: e['codigo'], mensaje: e['mensaje'] })),
    observaciones: d['observaciones'] ?? null,
    webhookDelivered: d['webhook_delivered'] ?? null,
    webhookLastError: d['webhook_last_error'] ?? '',
  }
}

export function listaComprobantesFromJson(d: Json): ListaComprobantesResult {
  return { items: (d['items'] as Json[]).map(emisionFromJson), count: d['count'] }
}

export function loteItemFromJson(d: Json): LoteItemResult {
  const emision = d['emision']
  return {
    idempotencyKey: d['idempotency_key'],
    ok: d['ok'],
    emision: emision == null ? null : emisionFromJson(emision),
    error: d['error'] ?? null,
    statusCode: d['status_code'] ?? null,
  }
}
