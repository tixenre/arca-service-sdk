/**
 * El sellado tiene que producir algo que abra la implementación del OTRO lado. Un test que
 * sólo cifrara y descifrara con este mismo código pasaría aunque el algoritmo entero
 * estuviera mal (otro padding, otro tamaño de nonce, el tag en otro lado): confirmaría que
 * es consistente consigo mismo, que no es lo que hace falta.
 *
 * Así que acá se sella con WebCrypto y se descifra con la librería de Python que usa el
 * SDK original, invirtiendo su `seal` paso por paso. Si alguno de los dos lados cambia el
 * algoritmo, esto se pone en rojo.
 */

import { execFileSync } from 'node:child_process'
import { generateKeyPairSync } from 'node:crypto'

import { describe, expect, it } from 'vitest'

import { seal } from '../src/crypto.js'
import { EnvelopeError } from '../src/errors.js'

function parDeClaves() {
  const { publicKey, privateKey } = generateKeyPairSync('rsa', { modulusLength: 2048 })
  return {
    publicPem: publicKey.export({ type: 'spki', format: 'pem' }).toString(),
    privatePem: privateKey.export({ type: 'pkcs8', format: 'pem' }).toString(),
  }
}

/**
 * Descifra con `cryptography` (Python), invirtiendo `arca_service_client.crypto.seal`:
 * RSA-OAEP con MGF1-SHA256/SHA256/sin label para la clave AES, y AES-256-GCM sin AAD para
 * el contenido.
 */
function descifrarConPython(privatePem: string, sealed: Record<string, string>): string {
  const script = `
import base64, json, sys
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

entrada = json.load(sys.stdin)
privada = serialization.load_pem_private_key(entrada["private_pem"].encode(), password=None)
sellado = entrada["sealed"]

assert sellado["v"] == "1", f'version inesperada: {sellado["v"]}'

aes_key = privada.decrypt(
    base64.b64decode(sellado["ek"]),
    padding.OAEP(mgf=padding.MGF1(algorithm=hashes.SHA256()), algorithm=hashes.SHA256(), label=None),
)
assert len(aes_key) == 32, f"la clave AES no es de 256 bits: {len(aes_key)}"

nonce = base64.b64decode(sellado["n"])
assert len(nonce) == 12, f"el nonce no es de 12 bytes: {len(nonce)}"

claro = AESGCM(aes_key).decrypt(nonce, base64.b64decode(sellado["ct"]), None)
sys.stdout.write(claro.decode())
`
  return execFileSync('python3', ['-c', script], {
    input: JSON.stringify({ private_pem: privatePem, sealed }),
    encoding: 'utf8',
  })
}

describe('seal', () => {
  it('produce algo que la implementación Python descifra', () => {
    const { publicPem, privatePem } = parDeClaves()
    // El shape real que sella `importarCredencial`, con acentos y comillas adentro para que
    // un problema de encoding no pase inadvertido.
    const secreto = JSON.stringify({
      key_pem: '-----BEGIN PRIVATE KEY-----\nMIIE"vQ"IBADAN\n-----END PRIVATE KEY-----\n',
      key_password: 'contraseña con ñ y "comillas"',
    })

    return seal(Buffer.from(secreto, 'utf8'), publicPem).then((sellado) => {
      expect(descifrarConPython(privatePem, sellado as unknown as Record<string, string>)).toBe(
        secreto,
      )
    })
  })

  it('descifra igual con key_password nulo', async () => {
    const { publicPem, privatePem } = parDeClaves()
    const secreto = JSON.stringify({ key_pem: 'x', key_password: null })

    const sellado = await seal(Buffer.from(secreto, 'utf8'), publicPem)

    expect(descifrarConPython(privatePem, sellado as unknown as Record<string, string>)).toBe(
      secreto,
    )
  })

  it('cada llamada usa un nonce y una clave AES distintos', async () => {
    const { publicPem } = parDeClaves()
    const plano = Buffer.from('lo mismo las dos veces', 'utf8')

    const a = await seal(plano, publicPem)
    const b = await seal(plano, publicPem)

    // Reusar el nonce con la misma clave rompe GCM por completo, así que esto no es un
    // detalle cosmético.
    expect(a.n).not.toBe(b.n)
    expect(a.ek).not.toBe(b.ek)
    expect(a.ct).not.toBe(b.ct)
  })

  it('rechaza un PEM que no es una clave pública', async () => {
    await expect(seal(Buffer.from('x'), 'no soy un PEM')).rejects.toBeInstanceOf(EnvelopeError)
  })

  it('rechaza un PEM con el label equivocado, diciendo cuál vino', async () => {
    const pem = '-----BEGIN RSA PUBLIC KEY-----\nAAAA\n-----END RSA PUBLIC KEY-----'
    await expect(seal(Buffer.from('x'), pem)).rejects.toThrow(/RSA PUBLIC KEY/)
  })
})
