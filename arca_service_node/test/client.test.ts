/**
 * El client se prueba contra un servidor HTTP local que guarda lo que le llegó, así cada
 * test verifica las DOS mitades por separado: qué se manda (método, ruta, query, body,
 * headers) y cómo se parsea lo que vuelve. Que una de las dos ande no dice nada de la otra.
 *
 * El servidor es `http://127.0.0.1`, que es el único caso donde el cliente acepta no-TLS
 * (ver `http.ts`). El par mTLS igual tiene que ser real y válido, porque el constructor lo
 * verifica antes de cualquier request.
 */

import { execFileSync } from 'node:child_process'
import { mkdtempSync, readFileSync } from 'node:fs'
import { createServer, type IncomingMessage, type Server } from 'node:http'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest'

import { ArcaServiceClient } from '../src/client.js'
import { CredentialsInvalidError, CredentialsRejectedError, NotFoundError } from '../src/errors.js'

interface Recibido {
  method: string
  url: string
  headers: NodeJS.Dict<string | string[]>
  body: string
}

interface Respuesta {
  status?: number
  json?: unknown
  text?: string
  contentType?: string
}

let server: Server
let baseUrl: string
let cert: string
let key: string
let otraKey: string

const recibidos: Recibido[] = []
/**
 * Las respuestas se consumen en orden. Es una cola y no una sola variable porque hay
 * métodos que hacen más de un request (`importarCredencial` pide primero la clave pública),
 * y encolar es determinístico: cambiar la variable "en el medio" depende del timing.
 */
const cola: Respuesta[] = []

function responder(...rs: Respuesta[]): void {
  cola.push(...rs)
}

/** Un par autofirmado descartable, generado al vuelo: no hay claves en el repo. */
function generarPar(dir: string, nombre: string): { cert: string; key: string } {
  const certPath = join(dir, `${nombre}.crt`)
  const keyPath = join(dir, `${nombre}.key`)
  execFileSync('openssl', [
    'req', '-x509', '-newkey', 'rsa:2048', '-nodes',
    '-keyout', keyPath, '-out', certPath,
    '-days', '1', '-subj', `/CN=${nombre}.arca-service.test`,
  ], { stdio: 'pipe' })
  return { cert: readFileSync(certPath, 'utf8'), key: readFileSync(keyPath, 'utf8') }
}

function cliente(): ArcaServiceClient {
  return new ArcaServiceClient({ baseUrl, apiKey: 'test-api-key', clientCert: cert, clientKey: key })
}

function ultimo(): Recibido {
  const r = recibidos.at(-1)
  if (!r) throw new Error('el servidor no recibió ningún request')
  return r
}

beforeAll(async () => {
  const dir = mkdtempSync(join(tmpdir(), 'arca-node-test-'))
  ;({ cert, key } = generarPar(dir, 'plataforma'))
  ;({ key: otraKey } = generarPar(dir, 'otra'))

  server = createServer((req: IncomingMessage, res) => {
    const chunks: Buffer[] = []
    req.on('data', (c: Buffer) => chunks.push(c))
    req.on('end', () => {
      recibidos.push({
        method: req.method ?? '',
        url: req.url ?? '',
        headers: req.headers,
        body: Buffer.concat(chunks).toString('utf8'),
      })
      const r = cola.shift() ?? { json: {} }
      res.writeHead(r.status ?? 200, {
        'Content-Type': r.contentType ?? 'application/json',
      })
      res.end(r.text !== undefined ? r.text : JSON.stringify(r.json ?? {}))
    })
  })

  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve))
  const dir2 = server.address()
  if (typeof dir2 === 'string' || dir2 === null) throw new Error('sin puerto')
  baseUrl = `http://127.0.0.1:${dir2.port}`
})

afterAll(() => {
  server.close()
})

afterEach(() => {
  recibidos.length = 0
  cola.length = 0
})

describe('construcción', () => {
  it('rechaza un cert y una clave que no son par, antes de cualquier request', () => {
    expect(
      () => new ArcaServiceClient({ baseUrl, apiKey: 'x', clientCert: cert, clientKey: otraKey }),
    ).toThrow(CredentialsInvalidError)
    // Nada llegó al servidor: el chequeo es local y previo.
    expect(recibidos).toHaveLength(0)
  })

  it('rechaza un PEM que no parsea', () => {
    expect(
      () => new ArcaServiceClient({ baseUrl, apiKey: 'x', clientCert: 'no soy un cert', clientKey: key }),
    ).toThrow(CredentialsInvalidError)
  })

  it('acepta el par por ruta además de por contenido', () => {
    const dir = mkdtempSync(join(tmpdir(), 'arca-node-rutas-'))
    execFileSync('openssl', [
      'req', '-x509', '-newkey', 'rsa:2048', '-nodes',
      '-keyout', join(dir, 'c.key'), '-out', join(dir, 'c.crt'),
      '-days', '1', '-subj', '/CN=porruta.test',
    ], { stdio: 'pipe' })
    expect(
      () =>
        new ArcaServiceClient({
          baseUrl,
          apiKey: 'x',
          clientCertPath: join(dir, 'c.crt'),
          clientKeyPath: join(dir, 'c.key'),
        }),
    ).not.toThrow()
  })

  it('exige que baseUrl sea https salvo contra loopback', async () => {
    const c = new ArcaServiceClient({
      baseUrl: 'http://arca.ejemplo.test',
      apiKey: 'x',
      clientCert: cert,
      clientKey: key,
    })
    // Sobre http remoto la API key viajaría en texto plano: se corta antes de mandar nada.
    await expect(c.porCuit('20301234563')).rejects.toThrow(/https/)
    c.close()
  })
})

describe('transporte', () => {
  it('manda Authorization y Accept en cada request', async () => {
    const c = cliente()
    responder({ json: { external_ref: 'cliente-1' } })

    await c.porCuit('20301234563')

    expect(ultimo().headers['authorization']).toBe('Bearer test-api-key')
    // Sin Accept explícito, un error de una capa anterior a la API vuelve como texto plano
    // en vez del sobre JSON.
    expect(ultimo().headers['accept']).toBe('application/json')
    c.close()
  })

  it('arma la ruta con el prefijo /api/v1 una sola vez, con o sin barra final', async () => {
    for (const raiz of [baseUrl, `${baseUrl}/`]) {
      const c = new ArcaServiceClient({ baseUrl: raiz, apiKey: 'x', clientCert: cert, clientKey: key })
      responder({ json: { external_ref: 'cliente-1' } })
      await c.porCuit('20301234563')
      expect(ultimo().url).toBe('/api/v1/clientes/por-cuit')
      c.close()
    }
  })
})

describe('métodos', () => {
  it('porCuit manda el cuit y parsea el external_ref', async () => {
    const c = cliente()
    responder({ json: { external_ref: 'cliente-1' } })

    const r = await c.porCuit('20301234563')

    expect(ultimo().method).toBe('POST')
    expect(ultimo().url).toBe('/api/v1/clientes/por-cuit')
    expect(JSON.parse(ultimo().body)).toEqual({ cuit: '20301234563' })
    expect(r.externalRef).toBe('cliente-1')
    c.close()
  })

  it('setFacturacion omite el campo que no se pasó y traduce a snake_case', async () => {
    const c = cliente()
    responder({ json: { iibb: '901-123456-7', nombre_comercial: null } })

    const r = await c.setFacturacion('cliente-1', { iibb: '901-123456-7' })

    expect(ultimo().method).toBe('PUT')
    expect(JSON.parse(ultimo().body)).toEqual({ iibb: '901-123456-7' })
    expect(r.nombreComercial).toBeNull()
    c.close()
  })

  it('generarCsr manda regenerar en false por default', async () => {
    const c = cliente()
    const ok = { json: { csr_pem: 'x', alias: 'cliente-1-2026' } }
    responder(ok, ok)

    await c.generarCsr('cliente-1', '20301234563')
    expect(JSON.parse(ultimo().body)).toEqual({ cuit: '20301234563', regenerar: false })

    await c.generarCsr('cliente-1', '20301234563', { regenerar: true })
    expect(JSON.parse(ultimo().body)).toEqual({ cuit: '20301234563', regenerar: true })
    c.close()
  })

  it('listarComprobantes arma la query con los filtros que se pasaron', async () => {
    const c = cliente()
    responder({ json: { items: [], count: 0 } })

    await c.listarComprobantes('cliente-1', {
      estado: 'issued',
      receptorCuit: '30-71234567-1',
      creadoDesde: '2026-08-01',
      limit: 10,
      offset: 20,
    })

    const url = new URL(`http://x${ultimo().url}`)
    expect(url.pathname).toBe('/api/v1/clientes/cliente-1/comprobantes')
    expect(url.searchParams.get('estado')).toBe('issued')
    expect(url.searchParams.get('receptor_cuit')).toBe('30-71234567-1')
    expect(url.searchParams.get('creado_desde')).toBe('2026-08-01')
    expect(url.searchParams.get('limit')).toBe('10')
    expect(url.searchParams.get('offset')).toBe('20')
    // Los que no se pasaron no viajan como vacíos.
    expect(url.searchParams.has('tipo')).toBe(false)
    expect(url.searchParams.has('creado_hasta')).toBe(false)
    c.close()
  })

  it('getComprobanteHtml manda layout=oficial por default y devuelve texto', async () => {
    const c = cliente()
    responder({ text: '<html>factura</html>', contentType: 'text/html' })

    const html = await c.getComprobanteHtml('cliente-1', 'factura-1')

    expect(ultimo().url).toBe(
      '/api/v1/clientes/cliente-1/comprobantes/factura-1/comprobante.html?layout=oficial',
    )
    expect(html).toBe('<html>factura</html>')
    c.close()
  })

  it('getComprobantePdf devuelve bytes, no un string', async () => {
    const c = cliente()
    responder({ text: '%PDF-1.4 fake', contentType: 'application/pdf' })

    const pdf = await c.getComprobantePdf('cliente-1', 'factura-1', { layout: 'simplificada' })

    expect(Buffer.isBuffer(pdf)).toBe(true)
    expect(pdf.subarray(0, 5).toString()).toBe('%PDF-')
    expect(ultimo().url).toContain('layout=simplificada')
    c.close()
  })

  it('los métodos de preview mandan el layout adentro del body, no en la query', async () => {
    const c = cliente()
    responder({ text: '<html>preview</html>', contentType: 'text/html' })

    await c.previewComprobanteHtml('cliente-1', {
      idempotencyKey: 'factura-1',
      concepto: 1,
      receptor: { consumidorFinal: true },
      items: [],
    })

    expect(ultimo().url).toBe('/api/v1/clientes/cliente-1/comprobantes/preview/comprobante.html')
    expect(JSON.parse(ultimo().body)['layout']).toBe('oficial')
    c.close()
  })

  it('emitirLoteComprobantes envuelve los ítems bajo su propia clave', async () => {
    const c = cliente()
    responder({ json: [] }, { json: [] })

    await c.emitirLoteComprobantes('cliente-1', [
      { idempotencyKey: 'f-1', concepto: 1, receptor: { consumidorFinal: true } },
    ])

    expect(Object.keys(JSON.parse(ultimo().body))).toEqual(['comprobantes'])

    await c.emitirLoteNotasCredito('cliente-1', [
      { idempotencyKey: 'nc-1', concepto: 1, receptor: { consumidorFinal: true } },
    ])
    expect(Object.keys(JSON.parse(ultimo().body))).toEqual(['notas_credito'])
    c.close()
  })

  it('crearSesionEmbebidaComprobante manda el receptor sólo si se pasó', async () => {
    const c = cliente()
    const respuesta = {
      json: { embed_url: 'https://arca.test/embed/facturar/x', expires_at: '2026-09-04T18:42:00.000000Z' },
    }

    responder(respuesta)
    const r = await c.crearSesionEmbebidaComprobante('cliente-1', {
      idempotencyKey: 'f-1',
      concepto: 1,
      items: [],
    })
    expect(JSON.parse(ultimo().body)).not.toHaveProperty('receptor')
    expect(r.embedUrl).toBe('https://arca.test/embed/facturar/x')
    expect(r.expiresAt).toBeInstanceOf(Date)
    expect(r.expiresAt.toISOString()).toBe('2026-09-04T18:42:00.000Z')

    responder(respuesta)
    await c.crearSesionEmbebidaComprobante('cliente-1', {
      idempotencyKey: 'f-1',
      concepto: 1,
      receptor: { cuit: '30712345671' },
      items: [],
    })
    expect(JSON.parse(ultimo().body)['receptor']['cuit']).toBe('30712345671')
    c.close()
  })

  it('importarCredencial pide la clave pública y sella la privada antes de mandarla', async () => {
    const c = cliente()
    const { publicPem } = (() => {
      const dir = mkdtempSync(join(tmpdir(), 'arca-node-pub-'))
      execFileSync('openssl', ['genrsa', '-out', join(dir, 'k.pem'), '2048'], { stdio: 'pipe' })
      execFileSync('openssl', ['rsa', '-in', join(dir, 'k.pem'), '-pubout', '-out', join(dir, 'pub.pem')], {
        stdio: 'pipe',
      })
      return { publicPem: readFileSync(join(dir, 'pub.pem'), 'utf8') }
    })()

    // Primero contesta la clave pública; después, el resultado del import. Encoladas de
    // entrada: el orden lo da la cola, no un sleep.
    responder({ json: { public_key_pem: publicPem } }, { json: { point_of_sale: 0, active: true } })
    const r = await c.importarCredencial('cliente-1', {
      cuit: '20301234563',
      certPem: '-----BEGIN CERTIFICATE-----\nx\n-----END CERTIFICATE-----',
      keyPem: '-----BEGIN PRIVATE KEY-----\nsecreto\n-----END PRIVATE KEY-----',
      keyPassword: 'clave',
    })

    expect(recibidos[0]?.url).toBe('/api/v1/envelope/clave-publica')
    const body = JSON.parse(ultimo().body)
    expect(ultimo().url).toBe('/api/v1/clientes/cliente-1/credencial/importar')
    expect(body['cuit']).toBe('20301234563')
    // Lo importante: la clave privada NO viaja en claro por ningún lado del body.
    expect(ultimo().body).not.toContain('secreto')
    expect(ultimo().body).not.toContain('clave')
    expect(body['sealed']).toMatchObject({ v: '1' })
    expect(typeof body['sealed']['ek']).toBe('string')
    expect(r.active).toBe(true)
    c.close()
  })
})

describe('errores', () => {
  it('un 401 llega como CredentialsRejectedError', async () => {
    const c = cliente()
    responder({
      status: 401,
      json: { error: { type: 'request', code: 'no_autenticado', message: 'No autenticado.' } },
    })

    await expect(c.porCuit('20301234563')).rejects.toBeInstanceOf(CredentialsRejectedError)
    c.close()
  })

  it('un 404 llega como NotFoundError, con el mensaje del servidor', async () => {
    const c = cliente()
    const noEncontrado = {
      status: 404,
      json: { error: { type: 'request', code: 'no_encontrado', message: 'Cliente no encontrado.' } },
    }
    responder(noEncontrado, noEncontrado)

    await expect(c.getComprobante('cliente-1', 'factura-1')).rejects.toThrow('Cliente no encontrado.')
    await expect(c.getComprobante('cliente-1', 'factura-1')).rejects.toBeInstanceOf(NotFoundError)
    c.close()
  })

  it('un error también se levanta en los endpoints que devuelven binario', async () => {
    const c = cliente()
    responder({
      status: 422,
      json: {
        error: {
          type: 'request',
          code: 'request_invalido',
          message: 'No se puede usar la tarjeta simplificada para este comprobante.',
        },
      },
    })

    // El caso real del layout simplificada: no devuelve un PDF recortado, devuelve 422.
    await expect(
      c.getComprobantePdf('cliente-1', 'factura-1', { layout: 'simplificada' }),
    ).rejects.toThrow(/tarjeta simplificada/)
    c.close()
  })
})
