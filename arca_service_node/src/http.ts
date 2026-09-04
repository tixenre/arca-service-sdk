/**
 * El transporte: mTLS + API key, sobre `node:https`. Sin dependencias.
 *
 * Se usa `https.request` y no el `fetch` global a propósito: para mandar un certificado de
 * cliente con `fetch` hay que armar un dispatcher de undici, que es una dependencia más y
 * una API que todavía se mueve. `https.Agent` con `cert`/`key` es estable desde hace años y
 * ya viene en Node.
 *
 * Las fallas de TRANSPORTE (timeout, DNS, conexión rechazada, TLS) se dejan propagar tal
 * cual: son errores nativos de Node y envolverlos mezclaría "arca-service respondió que
 * no" con "ni siquiera pudimos preguntarle", que son dos causas con remedios distintos.
 */

import http from 'node:http'
import https from 'node:https'
import type { IncomingHttpHeaders } from 'node:http'

export interface Transporte {
  agent: https.Agent
  apiKey: string
  baseUrl: string
  timeoutMs: number
}

export interface HttpResponse {
  statusCode: number
  headers: IncomingHttpHeaders
  body: Buffer
}

export interface RequestSpec {
  method: 'GET' | 'POST' | 'PUT'
  /** Ruta relativa a `{baseUrl}/api/v1`, empezando con `/`. */
  path: string
  query?: Record<string, string | number | undefined>
  json?: unknown
}

/**
 * Un `http://` remoto mandaría la API key y el body en texto plano. Se permite sólo contra
 * loopback, que es el único caso donde tiene sentido (un servidor de prueba local).
 */
function assertEsquemaSeguro(url: URL): void {
  if (url.protocol === 'https:') return
  const esLoopback = url.hostname === 'localhost' || url.hostname === '127.0.0.1' || url.hostname === '::1'
  if (url.protocol === 'http:' && esLoopback) return
  throw new Error(
    `baseUrl tiene que ser https:// (recibido ${url.protocol}//${url.hostname}). ` +
      'Sobre http:// la API key y el body viajarían en texto plano, y no habría mTLS: ' +
      'sólo se acepta contra localhost, para un servidor de prueba.',
  )
}

export function request(t: Transporte, spec: RequestSpec): Promise<HttpResponse> {
  const url = new URL(`${t.baseUrl.replace(/\/+$/, '')}/api/v1${spec.path}`)
  assertEsquemaSeguro(url)

  for (const [clave, valor] of Object.entries(spec.query ?? {})) {
    if (valor !== undefined) url.searchParams.set(clave, String(valor))
  }

  const cuerpo = spec.json === undefined ? undefined : Buffer.from(JSON.stringify(spec.json), 'utf8')

  const headers: Record<string, string> = {
    // `Accept` explícito y no el default: sin esto, un 404 de ruta inexistente (o
    // cualquier error servido por una capa anterior a la API) vuelve como texto plano en
    // vez del sobre `{"error": {...}}`, y no queda nada que parsear.
    Accept: 'application/json',
    Authorization: `Bearer ${t.apiKey}`,
  }
  if (cuerpo) {
    headers['Content-Type'] = 'application/json'
    headers['Content-Length'] = String(cuerpo.byteLength)
  }

  const esHttps = url.protocol === 'https:'
  const mod = esHttps ? https : http

  return new Promise<HttpResponse>((resolve, reject) => {
    const req = mod.request(
      {
        protocol: url.protocol,
        hostname: url.hostname,
        port: url.port || (esHttps ? 443 : 80),
        path: `${url.pathname}${url.search}`,
        method: spec.method,
        headers,
        ...(esHttps ? { agent: t.agent } : {}),
      },
      (res) => {
        const chunks: Buffer[] = []
        res.on('data', (c: Buffer) => chunks.push(c))
        res.on('error', reject)
        res.on('end', () => {
          resolve({
            statusCode: res.statusCode ?? 0,
            headers: res.headers,
            body: Buffer.concat(chunks),
          })
        })
      },
    )

    req.setTimeout(t.timeoutMs, () => {
      // `destroy` con un error propio para que el caller reciba algo diagnosticable en vez
      // de un socket que se cierra sin motivo aparente.
      req.destroy(new Error(`Timeout de ${t.timeoutMs} ms contra ${url.origin}`))
    })
    req.on('error', reject)
    if (cuerpo) req.write(cuerpo)
    req.end()
  })
}
