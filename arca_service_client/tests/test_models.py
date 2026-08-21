"""Tests de arca_service_client.models — sin red. `to_payload()` (request) y
`_from_json()` (response) son puro mapeo de datos; estos tests fijan el CONTRATO exacto
contra `apps/arca/schemas.py` real (nombres de campo, Decimal->string, date->ISO, qué se
omite vs qué se manda siempre) para que un futuro refactor no lo rompa en silencio."""

from __future__ import annotations

from datetime import date, datetime
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
    Tributo,
)


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
    data = {
        "cbte_tipo": 6,
        "cbte_letra": "B",
        "importe_neto": "1000.00",
        "importe_iva": "210.00",
        "importe_total": "1210.00",
        "importe_no_gravado": "0",
        "importe_exento": "0",
        "importe_tributos": "0",
    }
    result = PreviewResult._from_json(data)
    assert result.cbte_letra == "B"
    assert result.importe_total == Decimal("1210.00")
    assert isinstance(result.importe_total, Decimal)


def test_emision_result_from_json_pending():
    data = {
        "id": "550e8400-e29b-41d4-a716-446655440000",
        "idempotency_key": "factura-1",
        "tipo": "FACTURA",
        "estado": "pending",
    }
    result = EmisionResult._from_json(data)
    assert result.estado == "pending"
    assert result.numero is None
    assert result.cae == ""
    assert result.webhook_delivered is None


def test_emision_result_from_json_issued():
    data = {
        "id": "550e8400-e29b-41d4-a716-446655440000",
        "idempotency_key": "factura-1",
        "tipo": "FACTURA",
        "estado": "issued",
        "numero": 42,
        "cae": "71234567890123",
        "cae_vencimiento": "2026-08-28",
        "qr_url": "https://...",
        "webhook_delivered": True,
    }
    result = EmisionResult._from_json(data)
    assert result.numero == 42
    assert result.cae_vencimiento == date(2026, 8, 28)
    assert result.webhook_delivered is True


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
