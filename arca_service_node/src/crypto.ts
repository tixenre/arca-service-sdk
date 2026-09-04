/**
 * Sellado de payloads sensibles para `POST /clientes/{externalRef}/credencial/importar`.
 *
 * Cifrado híbrido RSA-OAEP + AES-256-GCM contra la clave pública vigente de arca-service.
 * No hay `unseal()` acá: eso lo corre arca-service del lado de adentro, con su clave
 * privada, que este paquete nunca ve.
 *
 * Por qué hace falta ESTO además de mTLS/TLS: la clave privada AFIP del contribuyente es
 * el dato más sensible que este flujo mueve. Sellarla en el body significa que ese texto
 * nunca existe en claro fuera de los dos extremos reales, ni siquiera si un proxy
 * intermedio o una herramienta de observabilidad llegara a loguear el body. Mismo patrón
 * que el "envelope encryption" de cualquier KMS de cloud: defensa en profundidad sobre TLS.
 *
 * El algoritmo es EXACTAMENTE el de `arca_service_client.crypto.seal` (Python) -- tiene que
 * serlo, porque lo abre el mismo servidor. Hay un test que sella acá y descifra con la
 * implementación Python real para que un cambio de un lado no pase silencioso.
 */

import { webcrypto } from 'node:crypto'

import { EnvelopeError } from './errors.js'

const AES_KEY_BITS = 256
const NONCE_SIZE = 12 // tamaño estándar de nonce para AES-GCM

/**
 * El shape exacto que espera el campo `sealed` del body de `.../credencial/importar`.
 * Los cuatro campos son base64. `v` es la versión del formato, hoy siempre `"1"`.
 */
export interface SealedPayload {
  v: string
  ek: string
  n: string
  ct: string
}

/** Saca el header/footer del PEM y devuelve el DER. */
function pemToDer(pem: string): Uint8Array {
  const match = /-----BEGIN ([A-Z ]+)-----([\s\S]*?)-----END \1-----/.exec(pem)
  if (!match) {
    throw new EnvelopeError('La clave pública no es una clave PEM válida.')
  }
  const label = match[1]
  if (label !== 'PUBLIC KEY') {
    // `PUBLIC KEY` es SubjectPublicKeyInfo, que es lo que publica el servicio y lo único
    // que importa WebCrypto. Un `RSA PUBLIC KEY` (PKCS#1) sería válido como PEM pero
    // WebCrypto no lo lee, así que se corta acá con un mensaje que dice qué pasó.
    throw new EnvelopeError(
      `La clave pública vino como "${label}" y se esperaba "PUBLIC KEY" (SubjectPublicKeyInfo).`,
    )
  }
  return Buffer.from((match[2] ?? '').replace(/\s+/g, ''), 'base64')
}

/**
 * Cifra `plaintext` para que SOLO arca-service (dueño de la privada correspondiente a
 * `recipientPublicKeyPem`, obtenida de `GET /envelope/clave-publica`) lo pueda leer.
 * Devuelve un objeto de strings base64 directamente JSON-serializable.
 */
export async function seal(
  plaintext: Uint8Array,
  recipientPublicKeyPem: string,
): Promise<SealedPayload> {
  const der = pemToDer(recipientPublicKeyPem)

  let publicKey: webcrypto.CryptoKey
  try {
    publicKey = await webcrypto.subtle.importKey(
      'spki',
      der,
      // RSA-OAEP en WebCrypto usa MGF1 con el mismo hash que `hash` y sin label, que es
      // exactamente la combinación del lado Python (MGF1-SHA256 / SHA256 / label=None).
      { name: 'RSA-OAEP', hash: 'SHA-256' },
      false,
      ['encrypt'],
    )
  } catch (cause) {
    // Un DER que no parsea, o una clave que no es RSA, caen los dos acá: WebCrypto no
    // distingue. El mensaje cubre las dos posibilidades en vez de afirmar una.
    throw new EnvelopeError('La clave pública no es una clave RSA válida.', { cause })
  }

  const aesKey = await webcrypto.subtle.generateKey(
    { name: 'AES-GCM', length: AES_KEY_BITS },
    true,
    ['encrypt'],
  )
  const nonce = webcrypto.getRandomValues(new Uint8Array(NONCE_SIZE))

  // Sin AAD, igual que del otro lado. El tag de 128 bits queda pegado al final del
  // ciphertext, que es también lo que hace la implementación Python.
  const ciphertext = await webcrypto.subtle.encrypt(
    { name: 'AES-GCM', iv: nonce },
    aesKey,
    plaintext,
  )
  const rawAesKey = await webcrypto.subtle.exportKey('raw', aesKey)
  const encryptedKey = await webcrypto.subtle.encrypt({ name: 'RSA-OAEP' }, publicKey, rawAesKey)

  return {
    v: '1',
    ek: Buffer.from(encryptedKey).toString('base64'),
    n: Buffer.from(nonce).toString('base64'),
    ct: Buffer.from(ciphertext).toString('base64'),
  }
}
