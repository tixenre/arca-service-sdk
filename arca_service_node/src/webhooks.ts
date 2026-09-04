/**
 * Verificación de la firma de un webhook de arca-service.
 *
 * HMAC-SHA256 con el `webhookSecret` de tu Plataforma sobre `timestamp + "." + body` -- el
 * timestamp entra DENTRO del material firmado, no alcanza con concatenarlo del lado del
 * integrador. Verificar la firma SIEMPRE antes de procesar el payload: un webhook sin
 * verificar es indistinguible de uno falsificado por cualquiera que conozca la URL.
 *
 * También se rechaza cualquier `X-Arca-Timestamp` fuera de una ventana de tolerancia
 * razonable (default: 5 minutos). Es protección de replay: sin ese chequeo, un webhook
 * legítimo capturado en tránsito se puede reenviar más tarde y la firma sigue verificando
 * OK, porque el HMAC por sí solo no expira.
 */

import { createHmac, timingSafeEqual } from 'node:crypto'

const TOLERANCE_SECONDS_DEFAULT = 300 // 5 minutos

export interface VerifyWebhookSignatureOptions {
  /** El body CRUDO del webhook, sin re-serializar. */
  payload: Uint8Array | string
  /** Header `X-Arca-Signature`. */
  signature: string
  /** Header `X-Arca-Timestamp`. */
  timestamp: string
  /** `Plataforma.webhook_secret`, generado/rotado por un operador de arca-service. */
  secret: string
  toleranceSeconds?: number
}

/**
 * `true` si `signature` es una firma HMAC-SHA256 válida de `payload` con `timestamp` y
 * `secret`, Y `timestamp` cae dentro de `toleranceSeconds` del reloj local. `false` ante
 * CUALQUIER problema (firma que no matchea, timestamp fuera de ventana, timestamp no
 * numérico) -- nunca tira, para que el caller pueda hacer simplemente
 * `if (!verifyWebhookSignature(...)) return new Response(null, { status: 401 })` sin un
 * try/catch propio.
 *
 * `payload` tiene que ser el body EXACTO tal cual llegó. Reserializar el JSON antes de
 * verificar (`JSON.stringify(JSON.parse(body))`) puede cambiar espaciado u orden de claves
 * y romper la firma aunque el contenido "sea el mismo". En Next.js eso significa leer el
 * body con `await request.text()` y verificar ESE string, no el resultado de `.json()`.
 */
export function verifyWebhookSignature({
  payload,
  signature,
  timestamp,
  secret,
  toleranceSeconds = TOLERANCE_SECONDS_DEFAULT,
}: VerifyWebhookSignatureOptions): boolean {
  // `Number.parseInt` acepta "12abc"; el timestamp tiene que ser un entero y nada más.
  if (!/^-?\d+$/.test(timestamp)) return false
  const ts = Number.parseInt(timestamp, 10)
  if (!Number.isFinite(ts)) return false

  const ahora = Math.floor(Date.now() / 1000)
  if (Math.abs(ahora - ts) > toleranceSeconds) return false

  const body = typeof payload === 'string' ? Buffer.from(payload, 'utf8') : Buffer.from(payload)
  const signedPayload = Buffer.concat([Buffer.from(`${timestamp}.`, 'utf8'), body])
  const expected = createHmac('sha256', secret).update(signedPayload).digest('hex')

  // `timingSafeEqual` tira si los largos no coinciden, así que ese caso se corta antes.
  // No filtra nada útil: el largo de un hex de SHA-256 es público y siempre el mismo.
  const a = Buffer.from(expected, 'utf8')
  const b = Buffer.from(signature, 'utf8')
  if (a.length !== b.length) return false
  return timingSafeEqual(a, b)
}
