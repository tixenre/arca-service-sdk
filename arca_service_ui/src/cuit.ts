/**
 * Normalizar/validar/formatear CUIT — sin dependencias, sin red.
 *
 * Mismo algoritmo mod-11 que `arca_fe.validadores`/`arca_service_client.validadores` —
 * duplicado acá, no importado: este paquete no depende de ningún otro de este monorepo
 * (mismo motivo por el que el lado Python tampoco importa desde `arca_fe` para esto —
 * aritmética pura, barata de mantener sincronizada a mano en el puñado de lugares que la
 * necesitan).
 *
 * Pensado para feedback instantáneo en un formulario propio — ANTES de llamar a
 * `backend.porCuit(cuit)` (ver `useArcaCredential`). Deliberadamente NO está cableado
 * adentro del hook: este paquete es headless a propósito (ver `useArcaCredential.ts`), el
 * producto decide cuándo y cómo usar esto. arca-service igual revalida esto mismo del
 * lado servidor — esto es una comodidad para el consumidor, no un reemplazo de esa
 * validación.
 */

const PESOS = [5, 4, 3, 2, 7, 6, 5, 4, 3, 2];

/**
 * Deja solo los dígitos de `raw` (tolera guiones, espacios, cualquier separador).
 * Devuelve el string de 11 dígitos, o `null` si no quedan exactamente 11 — ni error ni
 * excepción, es una normalización best-effort; el llamador decide qué hacer con `null`.
 */
export function normalizarCuit(raw: string | number | null | undefined): string | null {
  if (raw === null || raw === undefined || raw === "") return null;
  const digitos = String(raw).replace(/\D/g, "");
  return digitos.length === 11 ? digitos : null;
}

/**
 * Valida el dígito verificador (mod-11) de un CUIT/CUIL de 11 dígitos.
 * Normaliza primero (tolera guiones/espacios) — un CUIT con guiones y uno sin guiones dan
 * exactamente el mismo resultado.
 */
export function cuitValido(cuit: string | number | null | undefined): boolean {
  const n = normalizarCuit(cuit);
  if (n === null) return false;
  const digitos = n
    .slice(0, 10)
    .split("")
    .map((d) => Number(d));
  const suma = digitos.reduce((acc, d, i) => acc + d * PESOS[i], 0);
  const resto = 11 - (suma % 11);
  const verificador = resto === 11 ? 0 : resto === 10 ? 9 : resto;
  return verificador === Number(n[10]);
}

/**
 * Devuelve el CUIT formateado para MOSTRAR: `XX-XXXXXXXX-X` (el estándar de AFIP).
 * Nunca para guardar — normaliza (tolera guiones/espacios en la entrada) y arma el
 * formato con guiones. Lanza si no normaliza a 11 dígitos (no se intenta rellenar/truncar
 * — eso sería adivinar un dato mal formado).
 */
export function formatearCuit(cuit: string | number): string {
  const n = normalizarCuit(cuit);
  if (n === null) {
    throw new Error(`No se pudo normalizar '${cuit}' a un CUIT de 11 dígitos para formatear.`);
  }
  return `${n.slice(0, 2)}-${n.slice(2, 10)}-${n.slice(10)}`;
}
