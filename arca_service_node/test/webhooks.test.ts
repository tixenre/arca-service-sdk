/**
 * Igual que con el sellado, la firma la produce el OTRO lado, así que el chequeo central
 * compara contra la implementación Python en vez de contra una firma armada acá con el
 * mismo código que se está probando.
 */

import { execFileSync } from 'node:child_process'
import { createHmac } from 'node:crypto'
import { tmpdir } from 'node:os'

import { describe, expect, it } from 'vitest'

import { verifyWebhookSignature } from '../src/webhooks.js'

const SECRET = 'un-secreto-de-prueba'

/** La firma tal cual la calcula arca-service: HMAC-SHA256 sobre `timestamp + "." + body`. */
function firmar(timestamp: string, body: string): string {
  return createHmac('sha256', SECRET).update(`${timestamp}.${body}`).digest('hex')
}

function ahora(): string {
  return String(Math.floor(Date.now() / 1000))
}

describe('verifyWebhookSignature', () => {
  it('acepta una firma verificada por la implementación Python', () => {
    const ts = ahora()
    const body = JSON.stringify({ idempotency_key: 'factura-1', estado: 'issued' })
    const firma = firmar(ts, body)

    // Confirma contra el verificador real que este `firmar` no inventó nada: si el material
    // firmado fuera otro (por ejemplo sin el punto), Python diría que no.
    const script = `
import json, sys
from arca_service_client import verify_webhook_signature
e = json.load(sys.stdin)
print("SI" if verify_webhook_signature(
    e["payload"].encode(), e["signature"], e["timestamp"], e["secret"]
) else "NO")
`
    const salida = execFileSync('python3', ['-c', script], {
      input: JSON.stringify({ payload: body, signature: firma, timestamp: ts, secret: SECRET }),
      encoding: 'utf8',
      cwd: tmpdir(),
    }).trim()

    expect(salida).toBe('SI')
    expect(verifyWebhookSignature({ payload: body, signature: firma, timestamp: ts, secret: SECRET })).toBe(true)
  })

  it('acepta el payload como string o como bytes, indistinto', () => {
    const ts = ahora()
    const body = '{"a":1}'
    const firma = firmar(ts, body)

    expect(verifyWebhookSignature({ payload: body, signature: firma, timestamp: ts, secret: SECRET })).toBe(true)
    expect(
      verifyWebhookSignature({
        payload: Buffer.from(body, 'utf8'),
        signature: firma,
        timestamp: ts,
        secret: SECRET,
      }),
    ).toBe(true)
  })

  it('rechaza un body alterado aunque sea por un byte', () => {
    const ts = ahora()
    const firma = firmar(ts, '{"total":"100.00"}')

    expect(
      verifyWebhookSignature({
        payload: '{"total":"900.00"}',
        signature: firma,
        timestamp: ts,
        secret: SECRET,
      }),
    ).toBe(false)
  })

  it('rechaza una firma hecha con otro secreto', () => {
    const ts = ahora()
    const body = '{"a":1}'
    const otra = createHmac('sha256', 'otro-secreto').update(`${ts}.${body}`).digest('hex')

    expect(verifyWebhookSignature({ payload: body, signature: otra, timestamp: ts, secret: SECRET })).toBe(false)
  })

  it('rechaza un timestamp viejo, que es la protección de replay', () => {
    // La firma es válida: lo único fuera de lugar es el reloj. Sin este chequeo, un webhook
    // legítimo capturado en tránsito se puede reenviar para siempre.
    const viejo = String(Math.floor(Date.now() / 1000) - 3600)
    const body = '{"a":1}'

    expect(
      verifyWebhookSignature({
        payload: body,
        signature: firmar(viejo, body),
        timestamp: viejo,
        secret: SECRET,
      }),
    ).toBe(false)
  })

  it('acepta un timestamp viejo si se amplía la tolerancia a propósito', () => {
    const viejo = String(Math.floor(Date.now() / 1000) - 3600)
    const body = '{"a":1}'

    expect(
      verifyWebhookSignature({
        payload: body,
        signature: firmar(viejo, body),
        timestamp: viejo,
        secret: SECRET,
        toleranceSeconds: 7200,
      }),
    ).toBe(true)
  })

  it('devuelve false en vez de tirar ante entradas basura', () => {
    const body = '{"a":1}'
    const ts = ahora()

    for (const timestamp of ['', 'no-es-un-numero', '12abc', 'NaN']) {
      expect(
        verifyWebhookSignature({ payload: body, signature: firmar(ts, body), timestamp, secret: SECRET }),
      ).toBe(false)
    }
    // Una firma de otro largo no puede tirar en la comparación timing-safe.
    expect(verifyWebhookSignature({ payload: body, signature: 'corta', timestamp: ts, secret: SECRET })).toBe(
      false,
    )
  })
})
