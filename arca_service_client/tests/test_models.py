"""Tests de arca_service_client.models — sin red. `to_payload()` (request) y
`_from_json()` (response) son puro mapeo de datos; estos tests fijan el CONTRATO exacto
contra el shape real de la API (nombres de campo, Decimal->string, date->ISO, qué se
omite vs qué se manda siempre) para que un futuro refactor no lo rompa en silencio."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

from arca_service_client.models import (
    ComprobanteAsociado,
    ComprobanteInput,
    EmisionResult,
    ItemFactura,
    ItemIva,
    Opcional,
    PersonaArca,
    PreviewResult,
    SesionEmbebidaInput,
    SesionEmbebidaResult,
    Tributo,
)


def _importes_json(**overrides):
    d = {
        "neto": "1000.00",
        "iva": "0",
        "no_gravado": "0",
        "exento": "0",
        "tributos": "0",
        "total": "1000.00",
        "moneda": "PES",
        "cotizacion": "1",
    }
    d.update(overrides)
    return d


def _receptor_json(**overrides):
    d = {
        "doc_tipo": {"codigo": 96, "descripcion": "DNI"},
        "doc_nro": 12345678,
        "nombre": "",
        "domicilio": "",
        "condicion_iva": {"codigo": 5, "descripcion": "Consumidor Final", "fuente": "padron"},
    }
    d.update(overrides)
    return d


def _comprobante_minimo(**overrides):
    kwargs = dict(
        idempotency_key="factura-1",
        concepto=1,
        emisor_condicion_iva=1,
        receptor_doc_tipo=96,
        receptor_doc_nro="12345678",
        receptor_condicion_iva=5,
        fecha=date(2026, 8, 18),
    )
    kwargs.update(overrides)
    return ComprobanteInput(**kwargs)


def test_to_payload_campos_requeridos():
    payload = _comprobante_minimo().to_payload()
    assert payload["idempotency_key"] == "factura-1"
    assert payload["concepto"] == 1
    assert payload["receptor_doc_nro"] == "12345678"
    assert payload["fecha"] == "2026-08-18"


def test_to_payload_defaults_de_importe_van_como_string_no_float():
    payload = _comprobante_minimo().to_payload()
    assert payload["importe_neto"] == "0"
    assert payload["cotizacion"] == "1"
    assert isinstance(payload["importe_neto"], str)


def test_to_payload_omite_opcionales_ausentes():
    payload = _comprobante_minimo().to_payload()
    for campo in (
        "punto_venta",
        "fecha_serv_desde",
        "fecha_serv_hasta",
        "fecha_vto_pago",
        "alicuota_unica",
        "forzar_cbte_tipo",
        "comprobante_asociado",
    ):
        assert campo not in payload


def test_to_payload_incluye_opcionales_presentes():
    payload = _comprobante_minimo(
        punto_venta=3,
        fecha_serv_desde=date(2026, 8, 1),
        fecha_vto_pago=date(2026, 9, 1),
        alicuota_unica=5,
        forzar_cbte_tipo=201,
    ).to_payload()
    assert payload["punto_venta"] == 3
    assert payload["fecha_serv_desde"] == "2026-08-01"
    assert payload["fecha_vto_pago"] == "2026-09-01"
    assert payload["alicuota_unica"] == 5
    assert payload["forzar_cbte_tipo"] == 201


def test_to_payload_items_iva_tributos_opcionales_items():
    comprobante = _comprobante_minimo(
        items_iva=[ItemIva(alicuota_id=5, base_imponible=Decimal("1000.00"))],
        tributos=[
            Tributo(
                id=1, base_imponible=Decimal("100"), alicuota_pct=Decimal("3"), importe=Decimal("3")
            )
        ],
        opcionales=[Opcional(id="17", valor="alias-cbu")],
        items=[
            ItemFactura(
                descripcion="Consultoría", precio_unitario=Decimal("1000"), subtotal=Decimal("1000")
            )
        ],
    )
    payload = comprobante.to_payload()

    assert payload["items_iva"] == [{"alicuota_id": 5, "base_imponible": "1000.00"}]
    assert payload["tributos"] == [
        {"id": 1, "base_imponible": "100", "alicuota_pct": "3", "importe": "3", "desc": ""}
    ]
    assert payload["opcionales"] == [{"id": "17", "valor": "alias-cbu"}]
    assert payload["items"][0]["descripcion"] == "Consultoría"
    assert payload["items"][0]["cantidad"] == "1"  # default de ItemFactura


def test_to_payload_comprobante_asociado_para_nota_de_credito():
    comprobante = _comprobante_minimo(
        comprobante_asociado=ComprobanteAsociado(
            tipo=1, punto_venta=3, numero=100, cuit="20301234563"
        )
    )
    payload = comprobante.to_payload()
    assert payload["comprobante_asociado"] == {
        "tipo": 1,
        "punto_venta": 3,
        "numero": 100,
        "cuit": "20301234563",
    }


def test_to_payload_comprobante_asociado_omite_cuit_y_fecha_ausentes():
    comprobante = _comprobante_minimo(
        comprobante_asociado=ComprobanteAsociado(tipo=1, punto_venta=3, numero=100)
    )
    payload = comprobante.to_payload()
    assert payload["comprobante_asociado"] == {"tipo": 1, "punto_venta": 3, "numero": 100}


def test_preview_result_from_json():
    """`comprobante`/`importes` anidados -- valores verificados contra un preview real
    (ver `tests/test_contract.py`). Un preview no trae `moneda`/`cotizacion` en
    `importes` (nada se emitió todavía)."""
    data = {
        "comprobante": {"tipo": "factura", "letra": "B", "codigo_afip": 6},
        "importes": {
            "neto": "1000.00",
            "iva": "210.00",
            "total": "1210.00",
            "no_gravado": "0",
            "exento": "0",
            "tributos": "0",
        },
    }
    result = PreviewResult._from_json(data)
    assert result.comprobante.letra == "B"
    assert result.comprobante.codigo_afip == 6
    assert result.importes.total == Decimal("1210.00")
    assert isinstance(result.importes.total, Decimal)
    assert result.importes.moneda is None


def test_emision_result_from_json_pending():
    """`comprobante`/`importes`/`receptor` anidados, siempre presentes -- valores
    verificados contra una emisión real (ver `tests/test_contract.py`).
    `letra`/`codigo_afip`/`numero` en `None` mientras está `pending`: todavía no se le
    pidió el CAE a AFIP."""
    data = {
        "id": "550e8400-e29b-41d4-a716-446655440000",
        "idempotency_key": "factura-1",
        "estado": "pending",
        "comprobante": {
            "tipo": "factura",
            "letra": None,
            "codigo_afip": None,
            "punto_venta": None,
            "numero": None,
            "fecha": "2026-08-18",
        },
        "importes": _importes_json(),
        "receptor": _receptor_json(),
    }
    result = EmisionResult._from_json(data)
    assert result.estado == "pending"
    assert result.comprobante.tipo == "factura"
    assert result.comprobante.numero is None
    assert result.cae == ""
    assert result.webhook_delivered is None


def test_emision_result_from_json_issued():
    data = {
        "id": "550e8400-e29b-41d4-a716-446655440000",
        "idempotency_key": "factura-1",
        "estado": "issued",
        "comprobante": {
            "tipo": "factura",
            "letra": "B",
            "codigo_afip": 6,
            "punto_venta": 3,
            "numero": 42,
            "fecha": "2026-08-18",
        },
        "importes": _importes_json(),
        "receptor": _receptor_json(),
        "cae": "71234567890123",
        "cae_vencimiento": "2026-08-28",
        "qr_url": "https://...",
        "webhook_delivered": True,
    }
    result = EmisionResult._from_json(data)
    assert result.comprobante.numero == 42
    assert result.cae_vencimiento == date(2026, 8, 28)
    assert result.webhook_delivered is True


def test_emision_result_from_json_receptor_completo():
    """`receptor` real: `doc_tipo`/`condicion_iva` son sub-objetos con
    código+descripción, `doc_nro` viaja como número."""
    data = {
        "id": "550e8400-e29b-41d4-a716-446655440000",
        "idempotency_key": "factura-1",
        "estado": "issued",
        "comprobante": {"tipo": "factura", "fecha": "2026-08-18"},
        "importes": _importes_json(),
        "receptor": {
            "doc_tipo": {"codigo": 96, "descripcion": "DNI"},
            "doc_nro": 20111222,
            "nombre": "Juan Pérez",
            "domicilio": "Calle Falsa 123",
            "condicion_iva": {"codigo": 5, "descripcion": "Consumidor Final", "fuente": "padron"},
        },
    }
    result = EmisionResult._from_json(data)
    assert result.receptor.doc_tipo.codigo == 96
    assert result.receptor.doc_tipo.descripcion == "DNI"
    assert result.receptor.doc_nro == 20_111_222
    assert isinstance(result.receptor.doc_nro, int)
    assert result.receptor.nombre == "Juan Pérez"
    assert result.receptor.condicion_iva.fuente == "padron"


def test_emision_result_from_json_issued_con_observaciones():
    """`observaciones` -- comentarios de AFIP sobre un comprobante que SÍ autorizó (ver
    MIGRACION.md, punto 4): campo nuevo al lado de `errores`, no un reemplazo."""
    data = {
        "id": "550e8400-e29b-41d4-a716-446655440000",
        "idempotency_key": "factura-1",
        "estado": "issued",
        "comprobante": {"tipo": "factura", "numero": 42, "fecha": "2026-08-18"},
        "importes": _importes_json(),
        "receptor": _receptor_json(),
        "cae": "71234567890123",
        "errores": None,
        "observaciones": ["10063: el documento del receptor no figura en el padrón"],
    }
    result = EmisionResult._from_json(data)
    assert result.estado == "issued"
    assert result.errores is None
    assert result.observaciones == ["10063: el documento del receptor no figura en el padrón"]


def test_emision_result_from_json_sin_observaciones_queda_none():
    """El campo es opcional -- una respuesta que no lo manda (o lo manda `null`) no
    debe romper el parseo."""
    data = {
        "id": "550e8400-e29b-41d4-a716-446655440000",
        "idempotency_key": "factura-1",
        "estado": "pending",
        "comprobante": {"tipo": "factura", "fecha": "2026-08-18"},
        "importes": _importes_json(),
        "receptor": _receptor_json(),
    }
    result = EmisionResult._from_json(data)
    assert result.observaciones is None


def test_emision_result_from_json_error_con_errores():
    data = {
        "id": "550e8400-e29b-41d4-a716-446655440000",
        "idempotency_key": "factura-1",
        "estado": "error",
        "comprobante": {"tipo": "factura", "fecha": "2026-08-18"},
        "importes": _importes_json(),
        "receptor": _receptor_json(),
        "errores": ["10016: La fecha del comprobante está fuera de rango"],
    }
    result = EmisionResult._from_json(data)
    assert result.estado == "error"
    assert result.errores == ["10016: La fecha del comprobante está fuera de rango"]


def test_sesion_embebida_input_to_payload_no_incluye_receptor():
    payload = SesionEmbebidaInput(
        idempotency_key="factura-1",
        concepto=1,
        emisor_condicion_iva=1,
        fecha=date(2026, 8, 18),
    ).to_payload()
    assert payload["idempotency_key"] == "factura-1"
    assert payload["fecha"] == "2026-08-18"
    for campo in (
        "receptor_doc_tipo",
        "receptor_doc_nro",
        "receptor_condicion_iva",
        "receptor_nombre",
        "receptor_domicilio",
    ):
        assert campo not in payload


def test_sesion_embebida_input_to_payload_incluye_comprobante_asociado():
    payload = SesionEmbebidaInput(
        idempotency_key="nc-1",
        concepto=1,
        emisor_condicion_iva=1,
        fecha=date(2026, 8, 18),
        comprobante_asociado=ComprobanteAsociado(tipo=1, punto_venta=3, numero=100),
    ).to_payload()
    assert payload["comprobante_asociado"] == {"tipo": 1, "punto_venta": 3, "numero": 100}


def test_sesion_embebida_result_from_json():
    data = {
        "embed_url": "https://arca.test/embed/facturar/xyz",
        "expires_at": "2026-08-21T22:30:00.000000Z",
    }
    result = SesionEmbebidaResult._from_json(data)
    assert result.embed_url == "https://arca.test/embed/facturar/xyz"
    assert result.expires_at == datetime(2026, 8, 21, 22, 30, tzinfo=timezone.utc)


def test_persona_arca_from_json_minima():
    """Solo los campos obligatorios de PersonaArcaOut — todo lo demás tiene default en
    el dataclass, igual que en el schema real."""
    data = {
        "cuit": "20301234563",
        "razon_social": "Acme",
        "nombre": "",
        "apellido": "",
        "domicilio": "",
        "condicion_iva": "RESPONSABLE INSCRIPTO",
        "estado_clave": "ACTIVO",
    }
    persona = PersonaArca._from_json(data)
    assert persona.cuit == "20301234563"
    assert persona.actividades == ()
    assert persona.domicilio_fiscal is None


def test_persona_arca_from_json_con_actividades_y_domicilio_fiscal():
    data = {
        "cuit": "20301234563",
        "razon_social": "Acme",
        "nombre": "",
        "apellido": "",
        "domicilio": "",
        "condicion_iva": "RI",
        "estado_clave": "ACTIVO",
        "actividades": [
            {"id_actividad": 1, "descripcion": "Software", "periodo": 202601, "orden": 1}
        ],
        "domicilio_fiscal": {
            "direccion": "Calle 1",
            "localidad": "CABA",
            "provincia": "CABA",
            "id_provincia": 1,
            "codigo_postal": "1000",
            "tipo_domicilio": "FISCAL",
        },
        "fecha_contrato_social": "2020-01-01T00:00:00",
    }
    persona = PersonaArca._from_json(data)
    assert len(persona.actividades) == 1
    assert persona.actividades[0].descripcion == "Software"
    assert persona.domicilio_fiscal.localidad == "CABA"
    assert persona.fecha_contrato_social == datetime(2020, 1, 1)
