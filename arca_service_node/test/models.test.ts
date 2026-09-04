/**
 * El punto de estos tests no es que el payload "se vea bien": es que sea EL MISMO que arma
 * el SDK de Python, que es el que está en producción y verificado contra el servidor real.
 *
 * Por eso el chequeo central compara byte a byte contra `to_payload()` de Python en vez de
 * contra un objeto escrito a mano acá. Un literal escrito a mano sólo confirma que el
 * código hace lo que quien escribió el test creía; comparar contra la otra implementación
 * confirma que el contrato es el mismo, que es lo único que le importa al servidor.
 */

import { execFileSync } from 'node:child_process'
import { tmpdir } from 'node:os'

import { describe, expect, it } from 'vitest'

import {
  comprobanteToPayload,
  emisionFromJson,
  facturacionFromJson,
  listaComprobantesFromJson,
  loteItemFromJson,
  sesionEmbebidaToPayload,
  type ComprobanteInput,
  type SesionEmbebidaInput,
} from '../src/models.js'

/** Corre un snippet que tiene que imprimir el payload como JSON con las claves ordenadas. */
function payloadDePython(snippet: string): unknown {
  const script = `
import json
from decimal import Decimal
from datetime import date
from arca_service_client import (
    ComprobanteAsociado, ComprobanteInput, ItemFactura, Opcional, Receptor,
    SesionEmbebidaInput, Tributo,
)
${snippet}
print(json.dumps(entrada.to_payload(), sort_keys=True, default=str))
`
  // `cwd` neutro a propósito: parado en la raíz del repo, el directorio
  // `arca_service_client/` tapa como namespace package al paquete realmente instalado, y
  // el import falla con un "unknown location" que no dice nada.
  const salida = execFileSync('python3', ['-c', script], { encoding: 'utf8', cwd: tmpdir() })
  return JSON.parse(salida)
}

describe('comprobanteToPayload', () => {
  it('arma el mismo payload que el SDK de Python, en el caso mínimo', () => {
    const ts: ComprobanteInput = {
      idempotencyKey: 'factura-1',
      concepto: 1,
      receptor: { dni: '12345678' },
      items: [{ descripcion: 'Plan mensual', iva: '21', precioUnitario: '1000.00' }],
    }

    const py = payloadDePython(`
entrada = ComprobanteInput(
    idempotency_key="factura-1",
    concepto=1,
    receptor=Receptor(dni="12345678"),
    items=[ItemFactura(descripcion="Plan mensual", iva="21", precio_unitario=Decimal("1000.00"))],
)
`)

    expect(comprobanteToPayload(ts)).toEqual(py)
  })

  it('arma el mismo payload que Python con todos los campos poblados', () => {
    const ts: ComprobanteInput = {
      idempotencyKey: 'nc-1',
      concepto: 2,
      receptor: {
        cuit: '30712345671',
        condicionIva: 1,
        email: 'quien@ejemplo.test',
        consumidorFinal: false,
      },
      items: [
        {
          descripcion: 'Consultoría',
          iva: '10.5',
          precioFinal: '1105.00',
          codigo: 'SKU-1',
          cantidad: '2',
          unidadMedida: 'hora',
          bonificacionPct: '5',
          detalle: 'con detalle',
        },
      ],
      puntoVenta: 3,
      fecha: '2026-08-18',
      fechaServDesde: '2026-08-01',
      fechaServHasta: '2026-08-31',
      fechaVtoPago: '2026-09-10',
      moneda: 'DOL',
      forzarCbteTipo: 3,
      condicionVenta: 'Cuenta corriente',
      tributos: [
        {
          codigo: 99,
          baseImponible: '1000.00',
          alicuotaPct: '3',
          importe: '30.00',
          descripcion: 'IIBB',
        },
      ],
      opcionales: [{ codigo: '2101', valor: '0123456789012345678901' }],
      comprobanteAsociado: {
        tipo: 1,
        puntoVenta: 3,
        numero: 100,
        cuit: '30712345671',
        fecha: '2026-07-01',
        cae: '75123456789012',
        importeTotal: '1210.00',
      },
    }

    const py = payloadDePython(`
entrada = ComprobanteInput(
    idempotency_key="nc-1",
    concepto=2,
    receptor=Receptor(
        cuit="30712345671", condicion_iva=1, email="quien@ejemplo.test", consumidor_final=False
    ),
    items=[
        ItemFactura(
            descripcion="Consultoría",
            iva="10.5",
            precio_final=Decimal("1105.00"),
            codigo="SKU-1",
            cantidad=Decimal("2"),
            unidad_medida="hora",
            bonificacion_pct=Decimal("5"),
            detalle="con detalle",
        )
    ],
    punto_venta=3,
    fecha=date(2026, 8, 18),
    fecha_serv_desde=date(2026, 8, 1),
    fecha_serv_hasta=date(2026, 8, 31),
    fecha_vto_pago=date(2026, 9, 10),
    moneda="DOL",
    forzar_cbte_tipo=3,
    condicion_venta="Cuenta corriente",
    tributos=[
        Tributo(
            codigo=99,
            base_imponible=Decimal("1000.00"),
            alicuota_pct=Decimal("3"),
            importe=Decimal("30.00"),
            descripcion="IIBB",
        )
    ],
    opcionales=[Opcional(codigo="2101", valor="0123456789012345678901")],
    comprobante_asociado=ComprobanteAsociado(
        tipo=1,
        punto_venta=3,
        numero=100,
        cuit="30712345671",
        fecha=date(2026, 7, 1),
        cae="75123456789012",
        importe_total=Decimal("1210.00"),
    ),
)
`)

    expect(comprobanteToPayload(ts)).toEqual(py)
  })

  it('omite los campos opcionales en vez de mandarlos en null', () => {
    const payload = comprobanteToPayload({
      idempotencyKey: 'factura-1',
      concepto: 1,
      receptor: { consumidorFinal: true },
    })

    // Mandar `fecha: null` no es lo mismo que no mandarla: el servidor pone la fecha
    // argentina de hoy sólo cuando la clave está ausente.
    for (const clave of ['fecha', 'punto_venta', 'forzar_cbte_tipo', 'comprobante_asociado']) {
      expect(payload).not.toHaveProperty(clave)
    }
  })
})

describe('sesionEmbebidaToPayload', () => {
  it('sin receptor, arma el mismo payload que Python', () => {
    const ts: SesionEmbebidaInput = {
      idempotencyKey: 'factura-1',
      concepto: 1,
      items: [{ descripcion: 'Plan mensual', iva: '21', precioUnitario: '1000.00' }],
    }

    const py = payloadDePython(`
entrada = SesionEmbebidaInput(
    idempotency_key="factura-1",
    concepto=1,
    items=[ItemFactura(descripcion="Plan mensual", iva="21", precio_unitario=Decimal("1000.00"))],
)
`)

    expect(sesionEmbebidaToPayload(ts)).toEqual(py)
    expect(sesionEmbebidaToPayload(ts)).not.toHaveProperty('receptor')
  })

  it('con receptor, arma el mismo payload que Python', () => {
    const ts: SesionEmbebidaInput = {
      idempotencyKey: 'factura-1',
      concepto: 1,
      receptor: { cuit: '30712345671' },
      items: [],
    }

    const py = payloadDePython(`
entrada = SesionEmbebidaInput(
    idempotency_key="factura-1", concepto=1, receptor=Receptor(cuit="30712345671"), items=[]
)
`)

    expect(sesionEmbebidaToPayload(ts)).toEqual(py)
  })
})

describe('fromJson', () => {
  const emisionJson = {
    id: 'x',
    idempotency_key: 'factura-1',
    estado: 'issued',
    comprobante: {
      tipo: 'FACTURA',
      letra: 'B',
      codigo_afip: 6,
      punto_venta: 3,
      numero: 42,
      fecha: '2026-08-18',
    },
    importes: {
      neto: '1000.00',
      iva: '210.00',
      no_gravado: '0',
      exento: '0',
      tributos: '0',
      total: '1210.00',
      moneda: 'PES',
      cotizacion: '1',
    },
    receptor: {
      doc_tipo: { codigo: 96, descripcion: 'DNI' },
      doc_nro: 12345678,
      nombre: '',
      domicilio: '',
      condicion_iva: { codigo: 5, descripcion: 'Consumidor Final', fuente: 'padron' },
    },
    cae: '75123456789012',
    cae_vencimiento: '2026-08-28',
    qr_url: 'https://afip.test/qr',
    errores: null,
    observaciones: ['Documento no encontrado en el padrón'],
    webhook_delivered: true,
    webhook_last_error: '',
  }

  it('parsea una emisión completa a camelCase', () => {
    const e = emisionFromJson(emisionJson)

    expect(e.idempotencyKey).toBe('factura-1')
    expect(e.comprobante.codigoAfip).toBe(6)
    expect(e.caeVencimiento).toBe('2026-08-28')
    expect(e.qrUrl).toBe('https://afip.test/qr')
    expect(e.receptor.docTipo).toEqual({ codigo: 96, descripcion: 'DNI' })
    expect(e.receptor.condicionIva?.fuente).toBe('padron')
    expect(e.observaciones).toEqual(['Documento no encontrado en el padrón'])
    expect(e.webhookDelivered).toBe(true)
    // Los importes quedan como string, no como number: ver la nota en models.ts.
    expect(e.importes.total).toBe('1210.00')
    expect(typeof e.importes.total).toBe('string')
  })

  it('parsea los errores de AFIP como objetos, no como strings', () => {
    const e = emisionFromJson({
      ...emisionJson,
      estado: 'error',
      errores: [{ codigo: 10016, mensaje: 'Fecha fuera de rango' }],
    })

    expect(e.errores).toEqual([{ codigo: 10016, mensaje: 'Fecha fuera de rango' }])
  })

  it('tolera una emisión pending, con todo lo que todavía no existe en null', () => {
    const e = emisionFromJson({
      ...emisionJson,
      estado: 'pending',
      comprobante: { ...emisionJson.comprobante, letra: null, codigo_afip: null, numero: null },
      cae: '',
      cae_vencimiento: null,
      qr_url: '',
      observaciones: null,
      webhook_delivered: null,
    })

    expect(e.estado).toBe('pending')
    expect(e.comprobante.numero).toBeNull()
    expect(e.caeVencimiento).toBeNull()
    expect(e.observaciones).toBeNull()
  })

  it('facturacionFromJson deja en null el campo que nunca se configuró', () => {
    expect(facturacionFromJson({ iibb: '901-123456-7', nombre_comercial: null })).toEqual({
      iibb: '901-123456-7',
      nombreComercial: null,
    })
  })

  it('listaComprobantesFromJson distingue count de items.length', () => {
    const lista = listaComprobantesFromJson({ items: [emisionJson], count: 87 })

    expect(lista.items).toHaveLength(1)
    expect(lista.count).toBe(87)
  })

  it('loteItemFromJson parsea un ítem fallido sin emisión', () => {
    const item = loteItemFromJson({
      idempotency_key: 'factura-9',
      ok: false,
      emision: null,
      error: 'Ya existe con datos distintos',
      status_code: 409,
    })

    expect(item.ok).toBe(false)
    expect(item.emision).toBeNull()
    expect(item.statusCode).toBe(409)
  })
})
