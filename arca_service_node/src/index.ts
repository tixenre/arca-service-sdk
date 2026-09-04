/**
 * Cliente HTTP oficial para arca-service (facturación electrónica ARCA/AFIP) desde
 * Node/TypeScript.
 *
 *     import { ArcaServiceClient, Concepto } from '@tixenre/arca-service-node'
 *
 *     const client = new ArcaServiceClient({
 *       baseUrl: 'https://arca.mancino.dev',
 *       apiKey: process.env.ARCA_API_KEY!,
 *       clientCert: process.env.ARCA_CLIENT_CERT!,
 *       clientKey: process.env.ARCA_CLIENT_KEY!,
 *     })
 *
 *     // Primer llamado siempre: resuelve (o crea) el Cliente dueño de este CUIT.
 *     const { externalRef } = await client.porCuit('20301234563')
 *
 *     const emision = await client.emitirComprobante(externalRef, {
 *       idempotencyKey: 'factura-8231',
 *       concepto: Concepto.PRODUCTOS,
 *       receptor: { dni: '12345678' },
 *       items: [{ descripcion: 'Producto', iva: '21', precioUnitario: '1000.00' }],
 *     })
 *
 * **Server-side únicamente**: lleva el certificado mTLS y la API key de tu Plataforma, que
 * son credenciales de servidor. Nunca lo importes desde un componente de cliente.
 *
 * Sin dependencias de runtime: `node:https` para mTLS y WebCrypto para el sellado.
 */

export { ArcaServiceClient, LAYOUT_DEFAULT } from './client.js'
export type { ArcaServiceClientOptions, Layout } from './client.js'

export { CbteTipo, CondicionIva, Concepto, DocTipo } from './enums.js'

export { seal } from './crypto.js'
export type { SealedPayload } from './crypto.js'

export { verifyWebhookSignature } from './webhooks.js'
export type { VerifyWebhookSignatureOptions } from './webhooks.js'

export {
  AfipError,
  AfipRechazoError,
  AfipUnavailableError,
  ArcaServiceError,
  BonificadoLimiteError,
  ConfiguracionError,
  CredencialYaActivaError,
  CredentialsInvalidError,
  CredentialsRejectedError,
  CsrYaExisteError,
  EnvelopeError,
  IdempotencyConflictError,
  InternoError,
  NotaExcedeComprobanteError,
  NotFoundError,
  PuntoVentaNoHabilitadoError,
  RateLimitedError,
  RequestError,
  ServicioNoDisponibleError,
} from './errors.js'
export type { AfipErrorDetail } from './errors.js'

export type {
  Actividad,
  BonificadoResult,
  Caracterizacion,
  Categoria,
  Chequeo,
  CodigoAfip,
  ComponenteSociedad,
  ComprobanteAsociado,
  ComprobanteInfo,
  ComprobanteInput,
  CondicionIvaReceptor,
  ConexionAfipEmbedTokenResult,
  CredencialResult,
  Dependencia,
  DiagnosticoResult,
  Domicilio,
  EmbedTokenResult,
  EmisionResult,
  FacturacionResult,
  FechaISO,
  GenerarCsrResult,
  Importe,
  Importes,
  Impuesto,
  ItemFactura,
  ListaComprobantesResult,
  LoteItemResult,
  OnboardingResult,
  Opcional,
  PersonaArca,
  PreviewResult,
  PuntosVentaResult,
  PuntoVentaExcluido,
  PuntoVentaHabilitado,
  Receptor,
  ReceptorInfo,
  Regimen,
  SesionEmbebidaInput,
  SesionEmbebidaResult,
  Tributo,
} from './models.js'
