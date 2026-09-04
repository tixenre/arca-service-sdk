/**
 * Códigos tal cual los define AFIP. Portados a mano, NO reinventados: son los mismos
 * valores enteros que arca-service espera en el payload y devuelve en sus respuestas --
 * un mismatch acá sería un bug silencioso (ej. mandar `condicionIva: 4` pensando que es
 * Consumidor Final cuando en realidad es Exento).
 *
 * Mantenidos a mano, no importados de una dependencia -- evita arrastrar librerías
 * pesadas (SOAP/XML, criptografía) que un integrador HTTP puro no necesita.
 *
 * Son `enum` numéricos y no union types de literales a propósito: el valor que viaja es
 * el número, y tenerlo con nombre es justamente lo que evita el bug de arriba. Un
 * `number` crudo también se acepta donde van estos, para no obligar a nadie a importarlos.
 */

/** Condición frente al IVA (códigos `CondicionIVAReceptorId`, RG 5616). */
export enum CondicionIva {
  RESPONSABLE_INSCRIPTO = 1,
  EXENTO = 4,
  CONSUMIDOR_FINAL = 5,
  MONOTRIBUTO = 6,
}

/** Tipo de documento del receptor (tabla AFIP `FEParamGetTiposDoc`). */
export enum DocTipo {
  CUIT = 80,
  CUIL = 86,
  DNI = 96,
  /** Sin identificar (sólo válido en B/C, importes chicos). */
  CONSUMIDOR_FINAL = 99,
}

/**
 * Qué se factura (tabla AFIP `FEParamGetTiposConcepto`). `SERVICIOS`/
 * `PRODUCTOS_Y_SERVICIOS` exigen `fechaServDesde`/`fechaServHasta`/`fechaVtoPago` en
 * `ComprobanteInput` -- `PRODUCTOS` no (arca-service lo valida del lado servidor, este
 * paquete no duplica esa regla).
 */
export enum Concepto {
  PRODUCTOS = 1,
  SERVICIOS = 2,
  PRODUCTOS_Y_SERVICIOS = 3,
}

/**
 * Tipo de comprobante (tabla AFIP `FEParamGetTiposCbte`) -- para `forzarCbteTipo` en
 * `ComprobanteInput` (M/FCE MiPyme; A/B/C se seleccionan solas si se omite) y para
 * `ComprobanteAsociado.tipo` en una nota de crédito/débito.
 */
export enum CbteTipo {
  FACTURA_A = 1,
  NOTA_DEBITO_A = 2,
  NOTA_CREDITO_A = 3,
  FACTURA_B = 6,
  NOTA_DEBITO_B = 7,
  NOTA_CREDITO_B = 8,
  FACTURA_C = 11,
  NOTA_DEBITO_C = 12,
  NOTA_CREDITO_C = 13,
  FACTURA_M = 51,
  NOTA_DEBITO_M = 52,
  NOTA_CREDITO_M = 53,
  FACTURA_CRED_ELEC_MIPYME_A = 201,
  NOTA_DEBITO_CRED_ELEC_MIPYME_A = 202,
  NOTA_CREDITO_CRED_ELEC_MIPYME_A = 203,
  FACTURA_CRED_ELEC_MIPYME_B = 206,
  NOTA_DEBITO_CRED_ELEC_MIPYME_B = 207,
  NOTA_CREDITO_CRED_ELEC_MIPYME_B = 208,
  FACTURA_CRED_ELEC_MIPYME_C = 211,
  NOTA_DEBITO_CRED_ELEC_MIPYME_C = 212,
  NOTA_CREDITO_CRED_ELEC_MIPYME_C = 213,
}
