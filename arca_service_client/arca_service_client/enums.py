"""arca_service_client.enums — códigos tal cual los define AFIP. Portados a mano, NO
reinventados: son los mismos valores enteros que arca-service espera en el payload y
devuelve en sus respuestas — un mismatch acá sería un bug silencioso (ej. mandar
`receptor_condicion_iva=4` pensando que es Consumidor Final cuando en realidad es
Exento).

Mantenidos a mano, no importados de una dependencia -- evita arrastrar librerías
pesadas (SOAP/XML, criptografía) que un integrador HTTP puro no necesita."""

from __future__ import annotations

from enum import IntEnum


class CondicionIva(IntEnum):
    """Condición frente al IVA (códigos `CondicionIVAReceptorId`, RG 5616)."""

    RESPONSABLE_INSCRIPTO = 1
    EXENTO = 4
    CONSUMIDOR_FINAL = 5
    MONOTRIBUTO = 6


class DocTipo(IntEnum):
    """Tipo de documento del receptor (tabla AFIP `FEParamGetTiposDoc`)."""

    CUIT = 80
    CUIL = 86
    DNI = 96
    CONSUMIDOR_FINAL = 99  # sin identificar (sólo válido en B/C, importes chicos)


class Concepto(IntEnum):
    """Qué se factura (tabla AFIP `FEParamGetTiposConcepto`). `SERVICIOS`/
    `PRODUCTOS_Y_SERVICIOS` exigen `fecha_serv_desde`/`fecha_serv_hasta`/`fecha_vto_pago`
    en `ComprobanteInput` — `PRODUCTOS` no (arca-service lo valida del lado servidor,
    este paquete no duplica esa regla)."""

    PRODUCTOS = 1
    SERVICIOS = 2
    PRODUCTOS_Y_SERVICIOS = 3


class CbteTipo(IntEnum):
    """Tipo de comprobante (tabla AFIP `FEParamGetTiposCbte`) — para `forzar_cbte_tipo`
    en `ComprobanteInput` (M/FCE MiPyme; A/B/C se seleccionan solas si se omite) y para
    `ComprobanteAsociado.tipo` en una nota de crédito/débito."""

    FACTURA_A = 1
    NOTA_DEBITO_A = 2
    NOTA_CREDITO_A = 3
    FACTURA_B = 6
    NOTA_DEBITO_B = 7
    NOTA_CREDITO_B = 8
    FACTURA_C = 11
    NOTA_DEBITO_C = 12
    NOTA_CREDITO_C = 13
    FACTURA_M = 51
    NOTA_DEBITO_M = 52
    NOTA_CREDITO_M = 53
    FACTURA_CRED_ELEC_MIPYME_A = 201
    NOTA_DEBITO_CRED_ELEC_MIPYME_A = 202
    NOTA_CREDITO_CRED_ELEC_MIPYME_A = 203
    FACTURA_CRED_ELEC_MIPYME_B = 206
    NOTA_DEBITO_CRED_ELEC_MIPYME_B = 207
    NOTA_CREDITO_CRED_ELEC_MIPYME_B = 208
    FACTURA_CRED_ELEC_MIPYME_C = 211
    NOTA_DEBITO_CRED_ELEC_MIPYME_C = 212
    NOTA_CREDITO_CRED_ELEC_MIPYME_C = 213


class Alicuota(IntEnum):
    """Id de alícuota de IVA (tabla AFIP `FEParamGetTiposIva`) — para
    `ComprobanteInput.alicuota_unica`/`ItemIva.alicuota_id`. Alcanza con el id: el
    porcentaje lo resuelve arca-service, este paquete no calcula IVA."""

    IVA_0 = 3  # exento/no gravado dentro de un comprobante con IVA discriminado
    IVA_10_5 = 4
    IVA_21 = 5
    IVA_27 = 6
