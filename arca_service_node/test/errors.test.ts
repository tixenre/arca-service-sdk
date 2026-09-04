import { describe, expect, it } from 'vitest'

import {
  AfipRechazoError,
  AfipUnavailableError,
  ArcaServiceError,
  BonificadoLimiteError,
  ConfiguracionError,
  CredencialYaActivaError,
  CredentialsRejectedError,
  CsrYaExisteError,
  IdempotencyConflictError,
  InternoError,
  NotFoundError,
  RateLimitedError,
  RequestError,
  ServicioNoDisponibleError,
  raiseForStatus,
} from '../src/errors.js'

function sobre(type: string, code: string, message = 'x', extra: Record<string, unknown> = {}) {
  return JSON.stringify({ error: { type, code, message, ...extra } })
}

function alLevantar(status: number, body: string, headers = {}): ArcaServiceError {
  try {
    raiseForStatus(status, body, headers)
  } catch (e) {
    return e as ArcaServiceError
  }
  throw new Error('no levantó nada')
}

describe('raiseForStatus', () => {
  it('no hace nada con un status < 400', () => {
    expect(() => raiseForStatus(200, '{}')).not.toThrow()
    expect(() => raiseForStatus(201, '{}')).not.toThrow()
  })

  it('mapea cada code conocido a su clase con nombre', () => {
    const casos: [number, string, string, new (...a: never[]) => ArcaServiceError][] = [
      [404, 'request', 'no_encontrado', NotFoundError],
      [401, 'request', 'no_autenticado', CredentialsRejectedError],
      [403, 'request', 'origen_no_verificado', CredentialsRejectedError],
      [409, 'request', 'idempotency_key_reusada', IdempotencyConflictError],
      [409, 'request', 'csr_ya_existe', CsrYaExisteError],
      [409, 'request', 'credencial_ya_activa', CredencialYaActivaError],
      [409, 'configuracion', 'limite_bonificados_alcanzado', BonificadoLimiteError],
      [422, 'afip', 'afip_rechazo', AfipRechazoError],
      [502, 'afip', 'afip_sin_respuesta', AfipUnavailableError],
      [502, 'afip', 'afip_respuesta_ilegible', AfipUnavailableError],
      [503, 'interno', 'servicio_no_disponible', ServicioNoDisponibleError],
    ]

    for (const [status, type, code, Clase] of casos) {
      const err = alLevantar(status, sobre(type, code))
      expect(err, `${code} tendría que ser ${Clase.name}`).toBeInstanceOf(Clase)
      expect(err.code).toBe(code)
      expect(err.statusCode).toBe(status)
    }
  })

  it('un 401 no es un error de payload aunque el type sea request', () => {
    const err = alLevantar(401, sobre('request', 'no_autenticado', 'No autenticado.'))

    // Hereda de RequestError porque ese es el `type` que manda el servidor, pero tener
    // clase propia es justamente lo que evita confundirlo con un campo mal mandado.
    expect(err).toBeInstanceOf(RequestError)
    expect(err).toBeInstanceOf(CredentialsRejectedError)
    expect(err.message).toBe('No autenticado.')
  })

  it('un code desconocido cae en la clase de su type, no en un catch-all', () => {
    expect(alLevantar(422, sobre('request', 'code_que_todavia_no_existe'))).toBeInstanceOf(
      RequestError,
    )
    expect(alLevantar(422, sobre('configuracion', 'otro_code_nuevo'))).toBeInstanceOf(
      ConfiguracionError,
    )
  })

  it('un type irreconocible cae en InternoError', () => {
    expect(alLevantar(500, sobre('type_inventado', 'x'))).toBeInstanceOf(InternoError)
  })

  it('un body que no es el sobre no rompe: queda el texto crudo en message', () => {
    // Pasa de verdad si un proxy intermedio contesta antes que la API.
    const err = alLevantar(502, '<html>Bad Gateway</html>')

    expect(err).toBeInstanceOf(InternoError)
    expect(err.type).toBe('interno')
    expect(err.code).toBe('')
    expect(err.message).toBe('<html>Bad Gateway</html>')
  })

  it('propaga param y los códigos de AFIP sin masticar', () => {
    const conParam = alLevantar(
      422,
      sobre('request', 'nota_excede_comprobante', 'x', { param: 'comprobante_asociado' }),
    )
    expect(conParam.param).toBe('comprobante_asociado')

    const conAfip = alLevantar(
      422,
      sobre('afip', 'afip_rechazo', 'x', { afip: [{ codigo: 10016, mensaje: 'Fecha' }] }),
    )
    expect(conAfip.afip).toEqual([{ codigo: 10016, mensaje: 'Fecha' }])
  })

  it('RateLimitedError lee Retry-After, y queda en null si no vino', () => {
    const con = alLevantar(429, sobre('request', 'rate_limit'), { 'retry-after': '42' })
    expect(con).toBeInstanceOf(RateLimitedError)
    expect((con as RateLimitedError).retryAfter).toBe(42)

    const sin = alLevantar(429, sobre('request', 'rate_limit'))
    expect((sin as RateLimitedError).retryAfter).toBeNull()
  })

  it('todo lo que levanta es un Error de verdad y conserva su nombre', () => {
    const err = alLevantar(404, sobre('request', 'no_encontrado'))

    expect(err).toBeInstanceOf(Error)
    expect(err).toBeInstanceOf(ArcaServiceError)
    expect(err.name).toBe('NotFoundError')
    expect(err.stack).toBeTruthy()
  })
})
