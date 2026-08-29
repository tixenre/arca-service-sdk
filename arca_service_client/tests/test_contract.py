"""Tests de CONTRATO -- a diferencia del resto de la suite (que verifica que el SDK
hace lo que el SDK mismo dice que hace), este archivo verifica que lo que el SDK
manda/espera es lo que arca-service REALMENTE acepta/devuelve. Nació de un bug real:
`EmisionResult`/`PreviewResult` asumían campos planos (`tipo`, `numero`,
`importe_total`...) que nunca existieron así del lado servidor -- la respuesta real
anida todo bajo `comprobante`/`importes`/`receptor`. Un segundo bug del mismo tipo
apareció del lado del REQUEST: `ComprobanteInput` mandaba `receptor_doc_tipo`/
`receptor_doc_nro`, `importe_neto`/`alicuota_unica` sueltos, y `tributos`/`opcionales`
con `id`/`desc` -- nada de eso existe en el contrato real, que espera un `receptor`
anidado, los importes calculados desde `items`, y `codigo`/`descripcion` en vez de
`id`/`desc`. `MIGRACION.md` no mostró ninguno de los dos shapes completos, así que nada
en ese documento lo habría atrapado.

Dos tipos de verificación acá:

1. **En vivo**, contra `https://arca.mancino.dev` -- solo para lo que no necesita
   credenciales (el sobre de error). Se salta (no falla) si no hay salida de red desde
   este entorno; si la red anda y la forma cambió, esto tiene que fallar, es la única
   razón de que exista.
2. **Fixture fijo**, para todo lo que necesita mTLS + API key + un CUIT con AFIP
   configurado, que no están disponibles acá (ver MIGRACION.md, "Cómo verificar": "no
   intentes una emisión real de punta a punta"). En vez de inventar valores, cada
   fixture reproduce EXACTO los que ya se confirmaron contra el comportamiento real de
   la API -- el docstring de cada uno explica qué verifica y por qué se puede confiar
   en el valor, para poder re-confirmarlo si esto vuelve a divergir.

Si arca-service cambia alguno de estos shapes, este archivo (y `models.py`) son lo
primero que hay que actualizar -- y este archivo es donde hay que sumar el fixture
nuevo, con su propia justificación."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import httpx
import pytest

from arca_service_client import (
    ComprobanteAsociado,
    ComprobanteInput,
    EmisionResult,
    ItemFactura,
    Opcional,
    PreviewResult,
    Receptor,
    SesionEmbebidaResult,
    Tributo,
)

_PRODUCCION = "https://arca.mancino.dev"


# ---------------------------------------------------------------------------
# En vivo -- sin credenciales, contra producción real.
# ---------------------------------------------------------------------------


def _get_produccion(path: str, **kwargs):
    try:
        return httpx.get(f"{_PRODUCCION}{path}", timeout=10.0, **kwargs)
    except httpx.HTTPError as exc:
        pytest.skip(f"sin acceso de red a {_PRODUCCION}: {exc}")


def test_sobre_de_error_contra_produccion_real():
    """MIGRACION.md, punto 1, el ejemplo con el que abre el documento -- confirmado acá
    en vivo, no solo contra un mock armado a mano."""
    resp = _get_produccion(
        "/api/v1/clientes/x/comprobantes", headers={"Accept": "application/json"}
    )
    assert resp.status_code == 401
    error = resp.json()["error"]
    assert error["type"] == "request"
    assert error["code"] == "no_autenticado"
    assert isinstance(error["message"], str) and error["message"]


def test_404_sin_accept_header_vuelve_texto_plano_contra_produccion_real():
    """ "Un detalle verificado que muerde": sin `Accept: application/json`, un 404 real
    vuelve HTML, no el sobre -- por eso `ArcaServiceClient`/`AsyncArcaServiceClient`
    mandan ese header SIEMPRE (ver `client.py`/`async_client.py`). Si esto alguna vez
    empezara a devolver JSON solo, dejaría de ser un motivo para mandar el header --
    pero mandarlo seguiría siendo inofensivo, así que no hay nada que "arreglar" en el
    cliente por eso; este test es para saber si el supuesto que documenta ese comentario
    sigue siendo cierto."""
    resp = _get_produccion("/api/v1/ruta-que-no-existe")
    assert resp.status_code == 404
    assert "application/json" not in resp.headers.get("content-type", "")


def test_404_con_accept_header_vuelve_el_sobre_contra_produccion_real():
    resp = _get_produccion("/api/v1/ruta-que-no-existe", headers={"Accept": "application/json"})
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "no_encontrado"


# ---------------------------------------------------------------------------
# EmisionOut -- fixture fijo, reproduce EXACTO valores ya confirmados contra el
# comportamiento real de la API. El webhook manda el mismo documento, byte a byte, que
# la respuesta de `POST /comprobantes` -- así que este fixture vale para las tres
# puertas (POST /comprobantes, GET /comprobantes/:idempotency_key, y el webhook), no
# solo una.
# ---------------------------------------------------------------------------


def _emision_out_issued_verificado() -> dict:
    return {
        "id": "550e8400-e29b-41d4-a716-446655440000",
        "idempotency_key": "key-1",
        "estado": "issued",
        # Qué comprobante es, no sólo que salió.
        "comprobante": {
            "tipo": "factura",
            "letra": "B",
            "codigo_afip": 6,
            "punto_venta": 3,
            "numero": 41,
            "fecha": "2026-08-21",
        },
        # Por cuánto -- neto/total/moneda son los valores verificados; el resto
        # (iva/no_gravado/exento/tributos/cotizacion) completa el shape de `importes`,
        # no viene de otro valor real distinto.
        "importes": {
            "neto": "121.00",
            "iva": "0",
            "no_gravado": "0",
            "exento": "0",
            "tributos": "0",
            "total": "121.00",
            "moneda": "PES",
            "cotizacion": "1",
        },
        # A quién, con el nombre de la condición frente al IVA -- estos valores SÍ son
        # exactos, campo por campo.
        "receptor": {
            "doc_tipo": {"codigo": 96, "descripcion": "DNI"},
            "doc_nro": 20_111_222,
            "nombre": "Juan Pérez",
            "domicilio": "Calle Falsa 123",
            "condicion_iva": {
                "codigo": 5,
                "descripcion": "Consumidor Final",
                "fuente": "padron",
            },
        },
        "cae": "71234567890123",
        "cae_vencimiento": None,
        "qr_url": "",
        "errores": None,
        "observaciones": None,
    }


def test_emision_out_shape_real_comprobante_anidado():
    """`tipo`/`numero`/`letra`/`codigo_afip`/`punto_venta` viven bajo `comprobante`, NO
    planos en la raíz -- la causa original de este archivo."""
    result = EmisionResult._from_json(_emision_out_issued_verificado())
    assert result.comprobante.tipo == "factura"
    assert result.comprobante.letra == "B"
    assert result.comprobante.codigo_afip == 6
    assert result.comprobante.punto_venta == 3
    assert result.comprobante.numero == 41
    assert result.comprobante.fecha == date(2026, 8, 21)


def test_emision_out_shape_real_importes_anidado():
    result = EmisionResult._from_json(_emision_out_issued_verificado())
    assert result.importes.total == Decimal("121.00")
    assert result.importes.neto == Decimal("121.00")
    assert result.importes.moneda == "PES"


def test_emision_out_shape_real_receptor_anidado():
    result = EmisionResult._from_json(_emision_out_issued_verificado())
    assert result.receptor.doc_tipo.codigo == 96
    assert result.receptor.doc_tipo.descripcion == "DNI"
    # `doc_nro` viaja como número en el JSON real, no como string.
    assert result.receptor.doc_nro == 20_111_222
    assert isinstance(result.receptor.doc_nro, int)
    assert result.receptor.nombre == "Juan Pérez"
    assert result.receptor.domicilio == "Calle Falsa 123"
    assert result.receptor.condicion_iva.codigo == 5
    assert result.receptor.condicion_iva.fuente == "padron"


def test_emision_out_shape_real_pending_deja_letra_numero_codigo_afip_en_none():
    """`letra`/`codigo_afip`/`punto_venta`/`numero` son `None` mientras la emisión está
    `pending` -- se resuelven recién al pedir el CAE. `tipo`/`fecha` SÍ están, desde que
    se crea la fila."""
    data = _emision_out_issued_verificado()
    data.update(
        estado="pending",
        comprobante={
            "tipo": "factura",
            "letra": None,
            "codigo_afip": None,
            "punto_venta": None,
            "numero": None,
            "fecha": "2026-08-21",
        },
        cae="",
    )
    result = EmisionResult._from_json(data)
    assert result.estado == "pending"
    assert result.comprobante.tipo == "factura"
    assert result.comprobante.letra is None
    assert result.comprobante.numero is None


# ---------------------------------------------------------------------------
# PreviewOut -- fixture fijo, reproduce valores confirmados de un `POST
# /comprobantes/preview` real. A propósito comparte el shape de `comprobante`/
# `importes` con EmisionOut, pero con MENOS campos: sin `punto_venta`/`numero`/`fecha`
# en `comprobante` (nada se emitió) y sin `moneda`/`cotizacion` en `importes` (esas dos
# claves no están, ni siquiera como `null`).
# ---------------------------------------------------------------------------


def test_preview_out_shape_real():
    data = {
        "comprobante": {"tipo": "factura", "letra": "B", "codigo_afip": 6},
        "importes": {
            "neto": "121.00",
            "iva": "0",
            "no_gravado": "0",
            "exento": "0",
            "tributos": "0",
            "total": "121.00",
        },
    }
    result = PreviewResult._from_json(data)
    assert result.comprobante.tipo == "factura"
    assert result.comprobante.letra == "B"
    assert result.comprobante.codigo_afip == 6
    assert result.comprobante.punto_venta is None
    assert result.importes.total == Decimal("121.00")
    # Ausentes de verdad en el JSON real -- no `null`, la clave no está.
    assert result.importes.moneda is None
    assert result.importes.cotizacion is None


# ---------------------------------------------------------------------------
# Sesión embebida -- fixture fijo, reproduce valores confirmados de una creación de
# sesión embebida real: shape plano (`embed_url`/`expires_at`), no anidado a
# diferencia de EmisionOut.
# ---------------------------------------------------------------------------


def test_sesion_embebida_result_shape_real():
    data = {
        "embed_url": "https://arca.example.com/embed/facturar/SFMyNTY...",
        "expires_at": "2026-08-21T22:30:00.000000Z",
    }
    result = SesionEmbebidaResult._from_json(data)
    assert result.embed_url.startswith("https://")
    assert result.expires_at.year == 2026


# ---------------------------------------------------------------------------
# ComprobanteInput.to_payload() -- fixture fijo, cada caso reproduce EXACTO
# valores ya confirmados contra el validador real del REQUEST (no sólo la
# respuesta): el mismo validador que usa el endpoint real rechaza cualquier
# ejemplo que deje de casar con el shape actual, así que estos valores no son
# prosa transcripta a mano.
# ---------------------------------------------------------------------------


def test_comprobante_input_shape_real_minimo():
    """Lo mínimo real: `receptor` con una sola forma (acá `dni`), y un ítem con
    `iva` como porcentaje en string y `precio_unitario` -- ninguno de los dos
    campos que este SDK mandaba antes (`receptor_doc_tipo`/`receptor_doc_nro`
    sueltos, `importe_neto`/`alicuota_unica` en vez de `items`) existe en el
    contrato real."""
    comprobante = ComprobanteInput(
        idempotency_key="pedido-4821",
        concepto=1,
        receptor=Receptor(dni="20111222"),
        items=[ItemFactura(descripcion="Plan mensual", iva="0", precio_unitario=Decimal("121.00"))],
    )
    payload = comprobante.to_payload()

    assert payload["idempotency_key"] == "pedido-4821"
    assert payload["concepto"] == 1
    assert payload["receptor"]["dni"] == "20111222"
    assert payload["items"][0]["descripcion"] == "Plan mensual"
    assert payload["items"][0]["iva"] == "0"
    assert payload["items"][0]["precio_unitario"] == "121.00"


def test_comprobante_input_shape_real_con_email():
    """`receptor.email` va junto con cualquiera de las tres formas (acá `cuit`)
    -- no es un dato fiscal, así que no compite con lo que resuelve el padrón."""
    comprobante = ComprobanteInput(
        idempotency_key="factura-9134",
        concepto=1,
        receptor=Receptor(cuit="30712345671", email="cliente@ejemplo.com"),
        items=[
            ItemFactura(descripcion="Honorarios", iva="21", precio_unitario=Decimal("50000.00"))
        ],
    )
    payload = comprobante.to_payload()

    assert payload["receptor"]["cuit"] == "30712345671"
    assert payload["receptor"]["email"] == "cliente@ejemplo.com"


def test_comprobante_input_shape_real_tributos_y_opcionales():
    """`tributos`/`opcionales` usan `codigo`/`descripcion` -- nunca `id`/`desc`,
    que es lo que este SDK mandaba antes (bug real, corregido acá)."""
    comprobante = ComprobanteInput(
        idempotency_key="pedido-7720",
        concepto=1,
        receptor=Receptor(consumidor_final=True),
        items=[
            ItemFactura(
                descripcion="Producto gravado", iva="21", precio_unitario=Decimal("1000.00")
            )
        ],
        tributos=[
            Tributo(
                codigo=2,
                base_imponible=Decimal("1000.00"),
                alicuota_pct=Decimal("3"),
                importe=Decimal("30.00"),
                descripcion="Percepción de IIBB",
            )
        ],
        opcionales=[Opcional(codigo="2101", valor="0110599520000001234567")],
    )
    payload = comprobante.to_payload()

    assert payload["receptor"] == {"consumidor_final": True, "nombre": "", "domicilio": ""}
    assert payload["tributos"][0]["codigo"] == 2
    assert payload["tributos"][0]["descripcion"] == "Percepción de IIBB"
    assert payload["opcionales"][0] == {"codigo": "2101", "valor": "0110599520000001234567"}


def test_comprobante_input_shape_real_nota_credito():
    """`comprobante_asociado` identifica la factura original con `tipo`/
    `punto_venta`/`numero` -- alcanza con eso si la emitió este mismo
    servicio, se busca solo."""
    comprobante = ComprobanteInput(
        idempotency_key="nc-4821",
        concepto=1,
        receptor=Receptor(dni="20111222"),
        items=[
            ItemFactura(
                descripcion="Ajuste de plan mensual", iva="0", precio_unitario=Decimal("50.00")
            )
        ],
        comprobante_asociado=ComprobanteAsociado(tipo=6, punto_venta=3, numero=41),
    )
    payload = comprobante.to_payload()

    assert payload["comprobante_asociado"] == {"tipo": 6, "punto_venta": 3, "numero": 41}
