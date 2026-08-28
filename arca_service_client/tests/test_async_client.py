"""Tests de AsyncArcaServiceClient — mismo criterio y misma cobertura que
test_client.py (la contraparte sync), con `pytest_httpx` mockeando el transporte de
httpx igual para `httpx.AsyncClient` que para `httpx.Client`. NO se repite acá el porqué
de cada aserción (ya está en test_client.py) -- este archivo existe para PROBAR que la
versión async hace exactamente lo mismo, no para redocumentar el contrato."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
import pytest_asyncio

from arca_service_client import (
    AfipError,
    AfipErrorDetail,
    AfipRechazoError,
    AfipUnavailableError,
    AsyncArcaServiceClient,
    BonificadoLimiteError,
    ComprobanteAsociado,
    ComprobanteInput,
    ConfiguracionError,
    IdempotencyConflictError,
    InternoError,
    NotaExcedeComprobanteError,
    NotFoundError,
    PuntoVentaNoHabilitadoError,
    RateLimitedError,
    RequestError,
    ServicioNoDisponibleError,
    SesionEmbebidaInput,
)

_BASE_URL = "https://arca.test"
_API = "https://arca.test/api/v1"


@pytest_asyncio.fixture
async def client(client_cert_files):
    cert_path, key_path = client_cert_files
    c = AsyncArcaServiceClient(
        base_url=_BASE_URL,
        client_cert_path=cert_path,
        client_key_path=key_path,
        api_key="test-api-key",
    )
    yield c
    await c.aclose()


def _comprobante(**overrides):
    kwargs = dict(
        idempotency_key="factura-1",
        concepto=1,
        emisor_condicion_iva=1,
        receptor_doc_tipo=96,
        receptor_doc_nro="12345678",
        receptor_condicion_iva=5,
        fecha=date(2026, 8, 18),
        importe_neto=Decimal("1000.00"),
        alicuota_unica=5,
    )
    kwargs.update(overrides)
    return ComprobanteInput(**kwargs)


def _sesion_embebida(**overrides):
    kwargs = dict(
        idempotency_key="factura-1",
        concepto=1,
        emisor_condicion_iva=1,
        fecha=date(2026, 8, 18),
        importe_neto=Decimal("1000.00"),
        alicuota_unica=5,
    )
    kwargs.update(overrides)
    return SesionEmbebidaInput(**kwargs)


def _error(type: str, code: str, message: str, **extra):
    """Arma `{"error": {...}}` -- el sobre único de toda la API desde esta migración
    (ver MIGRACION.md, punto 1). `**extra` para `param`/`afip` cuando aplican."""
    return {"error": {"type": type, "code": code, "message": message, **extra}}


# ---------------------------------------------------------------------------
# base_url / auth
# ---------------------------------------------------------------------------


async def test_base_url_agrega_prefijo_api_v1_y_saca_barra_final(client_cert_files):
    cert_path, key_path = client_cert_files
    c = AsyncArcaServiceClient(
        base_url="https://arca.test/",  # con barra final
        client_cert_path=cert_path,
        client_key_path=key_path,
        api_key="x",
    )
    assert str(c._http.base_url) == "https://arca.test/api/v1/"
    await c.aclose()


async def test_manda_authorization_bearer_con_la_api_key(client, httpx_mock):
    httpx_mock.add_response(
        method="GET",
        url=f"{_API}/clientes/cliente-1/comprobantes/factura-1",
        match_headers={"Authorization": "Bearer test-api-key"},
        json={"id": "x", "idempotency_key": "factura-1", "tipo": "FACTURA", "estado": "pending"},
    )
    await client.get_comprobante("cliente-1", "factura-1")


async def test_manda_accept_application_json(client, httpx_mock):
    httpx_mock.add_response(
        method="GET",
        url=f"{_API}/clientes/cliente-1/comprobantes/factura-1",
        match_headers={"Accept": "application/json"},
        json={"id": "x", "idempotency_key": "factura-1", "tipo": "FACTURA", "estado": "pending"},
    )
    await client.get_comprobante("cliente-1", "factura-1")


async def test_context_manager_cierra_el_cliente_http(client_cert_files):
    cert_path, key_path = client_cert_files
    async with AsyncArcaServiceClient(
        base_url=_BASE_URL, client_cert_path=cert_path, client_key_path=key_path, api_key="x"
    ) as c:
        assert not c._http.is_closed
    assert c._http.is_closed


# ---------------------------------------------------------------------------
# Cliente — onboarding por CUIT + vínculo (Fase 12)
# ---------------------------------------------------------------------------


async def test_por_cuit(client, httpx_mock):
    httpx_mock.add_response(
        method="POST",
        url=f"{_API}/clientes/por-cuit",
        match_json={"cuit": "20301234563"},
        json={"external_ref": "cliente-1"},
    )
    result = await client.por_cuit("20301234563")
    assert result.external_ref == "cliente-1"


async def test_por_cuit_cuit_invalido_levanta_request_error(client, httpx_mock):
    httpx_mock.add_response(
        method="POST",
        url=f"{_API}/clientes/por-cuit",
        status_code=422,
        json=_error("request", "campo_invalido", "'123' no es un CUIT/CUIL válido.", param="cuit"),
    )
    with pytest.raises(RequestError, match="CUIT/CUIL válido") as exc_info:
        await client.por_cuit("123")
    assert exc_info.value.code == "campo_invalido"


async def test_set_bonificado_activar(client, httpx_mock):
    httpx_mock.add_response(
        method="PUT",
        url=f"{_API}/clientes/cliente-1/bonificado",
        match_json={"bonificado": True},
        json={"bonificado": True},
    )
    result = await client.set_bonificado("cliente-1", True)
    assert result.bonificado is True


async def test_set_bonificado_desactivar(client, httpx_mock):
    httpx_mock.add_response(
        method="PUT",
        url=f"{_API}/clientes/cliente-1/bonificado",
        match_json={"bonificado": False},
        json={"bonificado": False},
    )
    result = await client.set_bonificado("cliente-1", False)
    assert result.bonificado is False


async def test_set_bonificado_409_levanta_bonificado_limite_error_no_idempotency_conflict(
    client, httpx_mock
):
    """El 409 de `set_bonificado` es un tipo DISTINTO al de idempotencia (ver
    `BonificadoLimiteError` en exceptions.py) -- este test existe específicamente para
    que un futuro cambio no lo confunda con `IdempotencyConflictError` sin que nada lo
    note (los dos comparten status_code, solo el SITIO DE LLAMADA distingue -- hoy
    arca-service ni siquiera los distingue por `code`)."""
    httpx_mock.add_response(
        method="PUT",
        url=f"{_API}/clientes/cliente-1/bonificado",
        status_code=409,
        json=_error(
            "request",
            "idempotency_key_reusada",
            "Se alcanzó el límite de seguridad de bonificados para esta plataforma.",
        ),
    )
    with pytest.raises(BonificadoLimiteError) as exc_info:
        await client.set_bonificado("cliente-1", True)
    assert not isinstance(exc_info.value, IdempotencyConflictError)
    assert exc_info.value.status_code == 409


async def test_set_bonificado_404_cliente_no_vinculado_levanta_not_found_error(client, httpx_mock):
    httpx_mock.add_response(
        method="PUT",
        url=f"{_API}/clientes/cliente-1/bonificado",
        status_code=404,
        json=_error("request", "no_encontrado", "Cliente no encontrado."),
    )
    with pytest.raises(NotFoundError):
        await client.set_bonificado("cliente-1", True)


# ---------------------------------------------------------------------------
# Onboarding de credencial
# ---------------------------------------------------------------------------


async def test_generar_csr(client, httpx_mock):
    httpx_mock.add_response(
        method="POST",
        url=f"{_API}/clientes/cliente-1/csr",
        match_json={"cuit": "20301234563", "regenerar": False},
        json={"csr_pem": "-----BEGIN CERTIFICATE REQUEST-----...", "alias": "cliente-1-2026"},
    )
    result = await client.generar_csr("cliente-1", "20301234563")
    assert result.alias == "cliente-1-2026"


async def test_completar_credencial(client, httpx_mock):
    httpx_mock.add_response(
        method="POST",
        url=f"{_API}/clientes/cliente-1/credencial/completar",
        match_json={"cert_pem": "-----BEGIN CERTIFICATE-----...", "point_of_sale": 3},
        json={"point_of_sale": 3, "active": True},
    )
    result = await client.completar_credencial(
        "cliente-1", "-----BEGIN CERTIFICATE-----...", point_of_sale=3
    )
    assert result.point_of_sale == 3
    assert result.active is True


async def test_importar_credencial_pide_clave_publica_y_sella_antes_de_mandar(client, httpx_mock):
    httpx_mock.add_response(
        method="GET",
        url=f"{_API}/envelope/clave-publica",
        json={"public_key_pem": _clave_publica_de_test()},
    )

    capturado = {}

    def _responder(request):
        import json as _json

        capturado["body"] = _json.loads(request.content)
        return __import__("httpx").Response(200, json={"point_of_sale": 0, "active": True})

    httpx_mock.add_callback(
        _responder, method="POST", url=f"{_API}/clientes/cliente-1/credencial/importar"
    )

    result = await client.importar_credencial(
        "cliente-1",
        "20301234563",
        "-----BEGIN CERTIFICATE-----...",
        "-----BEGIN PRIVATE KEY-----...",
    )

    assert result.active is True
    body = capturado["body"]
    assert body["cuit"] == "20301234563"
    assert body["point_of_sale"] == 0
    assert set(body["sealed"].keys()) == {"v", "ek", "n", "ct"}


async def test_diagnosticar_credencial(client, httpx_mock):
    httpx_mock.add_response(
        method="POST",
        url=f"{_API}/clientes/cliente-1/credencial/diagnostico",
        json={
            "chequeos": [
                {"check": "cert_vigente", "ok": True, "bloqueante": True, "mensaje": "OK"}
            ],
            "listo": True,
        },
    )
    result = await client.diagnosticar_credencial("cliente-1")
    assert result.listo is True
    assert result.chequeos[0].check == "cert_vigente"


async def test_listar_puntos_de_venta(client, httpx_mock):
    httpx_mock.add_response(
        method="GET",
        url=f"{_API}/clientes/cliente-1/credencial/puntos-venta",
        json={
            "habilitados": [{"nro": 3, "emision_tipo": "CAE"}],
            "excluidos": [{"nro": 4, "motivo": "sin factura electrónica habilitada"}],
        },
    )
    result = await client.listar_puntos_de_venta("cliente-1")
    assert result.habilitados[0].nro == 3
    assert result.excluidos[0].motivo == "sin factura electrónica habilitada"


# ---------------------------------------------------------------------------
# Padrón
# ---------------------------------------------------------------------------


async def test_consultar_padron(client, httpx_mock):
    httpx_mock.add_response(
        method="GET",
        url=f"{_API}/clientes/cliente-1/padron/20301234563",
        json={
            "cuit": "20301234563",
            "razon_social": "Acme",
            "nombre": "",
            "apellido": "",
            "domicilio": "",
            "condicion_iva": "RI",
            "estado_clave": "ACTIVO",
        },
    )
    persona = await client.consultar_padron("cliente-1", "20301234563")
    assert persona.razon_social == "Acme"


# ---------------------------------------------------------------------------
# Preview / emisión
# ---------------------------------------------------------------------------


async def test_preview_comprobante(client, httpx_mock):
    httpx_mock.add_response(
        method="POST",
        url=f"{_API}/clientes/cliente-1/comprobantes/preview",
        json={
            "cbte_tipo": 6,
            "cbte_letra": "B",
            "importe_neto": "1000.00",
            "importe_iva": "210.00",
            "importe_total": "1210.00",
            "importe_no_gravado": "0",
            "importe_exento": "0",
            "importe_tributos": "0",
        },
    )
    result = await client.preview_comprobante("cliente-1", _comprobante())
    assert result.cbte_letra == "B"
    assert result.importe_total == Decimal("1210.00")


async def test_emitir_comprobante_devuelve_pending(client, httpx_mock):
    httpx_mock.add_response(
        method="POST",
        url=f"{_API}/clientes/cliente-1/comprobantes",
        status_code=202,
        json={"id": "x", "idempotency_key": "factura-1", "tipo": "FACTURA", "estado": "pending"},
    )
    result = await client.emitir_comprobante("cliente-1", _comprobante())
    assert result.estado == "pending"


async def test_emitir_nota_credito_manda_al_endpoint_de_notas_credito(client, httpx_mock):
    httpx_mock.add_response(
        method="POST",
        url=f"{_API}/clientes/cliente-1/notas-credito",
        status_code=202,
        json={"id": "x", "idempotency_key": "nc-1", "tipo": "NOTA_CREDITO", "estado": "pending"},
    )
    nota = _comprobante(
        idempotency_key="nc-1",
        comprobante_asociado=ComprobanteAsociado(tipo=1, punto_venta=3, numero=100),
    )
    result = await client.emitir_nota_credito("cliente-1", nota)
    assert result.tipo == "NOTA_CREDITO"


async def test_preview_nota_debito(client, httpx_mock):
    httpx_mock.add_response(
        method="POST",
        url=f"{_API}/clientes/cliente-1/notas-debito/preview",
        json={
            "cbte_tipo": 7,
            "cbte_letra": "B",
            "importe_neto": "1000.00",
            "importe_iva": "210.00",
            "importe_total": "1210.00",
            "importe_no_gravado": "0",
            "importe_exento": "0",
            "importe_tributos": "0",
        },
    )
    nota = _comprobante(
        idempotency_key="nd-1",
        comprobante_asociado=ComprobanteAsociado(tipo=1, punto_venta=3, numero=100),
    )
    result = await client.preview_nota_debito("cliente-1", nota)
    assert result.cbte_letra == "B"


async def test_emitir_nota_debito_manda_al_endpoint_de_notas_debito(client, httpx_mock):
    httpx_mock.add_response(
        method="POST",
        url=f"{_API}/clientes/cliente-1/notas-debito",
        status_code=202,
        json={"id": "x", "idempotency_key": "nd-1", "tipo": "NOTA_DEBITO", "estado": "pending"},
    )
    nota = _comprobante(
        idempotency_key="nd-1",
        comprobante_asociado=ComprobanteAsociado(tipo=1, punto_venta=3, numero=100),
    )
    result = await client.emitir_nota_debito("cliente-1", nota)
    assert result.tipo == "NOTA_DEBITO"


async def test_get_comprobante_issued(client, httpx_mock):
    httpx_mock.add_response(
        method="GET",
        url=f"{_API}/clientes/cliente-1/comprobantes/factura-1",
        json={
            "id": "x",
            "idempotency_key": "factura-1",
            "tipo": "FACTURA",
            "estado": "issued",
            "numero": 42,
            "cae": "71234567890123",
        },
    )
    result = await client.get_comprobante("cliente-1", "factura-1")
    assert result.numero == 42
    assert result.cae == "71234567890123"


async def test_get_comprobante_con_observaciones(client, httpx_mock):
    httpx_mock.add_response(
        method="GET",
        url=f"{_API}/clientes/cliente-1/comprobantes/factura-1",
        json={
            "id": "x",
            "idempotency_key": "factura-1",
            "tipo": "FACTURA",
            "estado": "issued",
            "numero": 42,
            "cae": "71234567890123",
            "errores": None,
            "observaciones": ["10063: el documento del receptor no figura en el padrón"],
        },
    )
    result = await client.get_comprobante("cliente-1", "factura-1")
    assert result.errores is None
    assert result.observaciones == ["10063: el documento del receptor no figura en el padrón"]


# ---------------------------------------------------------------------------
# Sesión embebida (iframe)
# ---------------------------------------------------------------------------


async def test_crear_sesion_embebida_comprobante(client, httpx_mock):
    capturado = {}

    def _responder(request):
        import json as _json

        capturado["body"] = _json.loads(request.content)
        return __import__("httpx").Response(
            201,
            json={
                "embed_url": "https://arca.test/embed/facturar/xyz",
                "expires_at": "2026-08-21T22:30:00.000000Z",
            },
        )

    httpx_mock.add_callback(
        _responder,
        method="POST",
        url=f"{_API}/clientes/cliente-1/comprobantes/sesion-embebida",
    )

    result = await client.crear_sesion_embebida_comprobante("cliente-1", _sesion_embebida())

    assert result.embed_url == "https://arca.test/embed/facturar/xyz"
    assert result.expires_at == datetime(2026, 8, 21, 22, 30, tzinfo=timezone.utc)
    assert "receptor_doc_tipo" not in capturado["body"]


async def test_crear_sesion_embebida_nota_credito_manda_al_endpoint_de_notas_credito(
    client, httpx_mock
):
    httpx_mock.add_response(
        method="POST",
        url=f"{_API}/clientes/cliente-1/notas-credito/sesion-embebida",
        status_code=201,
        json={
            "embed_url": "https://arca.test/embed/facturar/nc-xyz",
            "expires_at": "2026-08-21T22:30:00.000000Z",
        },
    )
    nota = _sesion_embebida(
        idempotency_key="nc-1",
        comprobante_asociado=ComprobanteAsociado(tipo=3, punto_venta=3, numero=100),
    )
    result = await client.crear_sesion_embebida_nota_credito("cliente-1", nota)
    assert result.embed_url == "https://arca.test/embed/facturar/nc-xyz"


async def test_crear_sesion_embebida_nota_debito_manda_al_endpoint_de_notas_debito(
    client, httpx_mock
):
    httpx_mock.add_response(
        method="POST",
        url=f"{_API}/clientes/cliente-1/notas-debito/sesion-embebida",
        status_code=201,
        json={
            "embed_url": "https://arca.test/embed/facturar/nd-xyz",
            "expires_at": "2026-08-21T22:30:00.000000Z",
        },
    )
    nota = _sesion_embebida(
        idempotency_key="nd-1",
        comprobante_asociado=ComprobanteAsociado(tipo=2, punto_venta=3, numero=100),
    )
    result = await client.crear_sesion_embebida_nota_debito("cliente-1", nota)
    assert result.embed_url == "https://arca.test/embed/facturar/nd-xyz"


# ---------------------------------------------------------------------------
# Documento renderizado
# ---------------------------------------------------------------------------


async def test_get_comprobante_html_manda_layout_default_oficial(client, httpx_mock):
    httpx_mock.add_response(
        method="GET",
        url=f"{_API}/clientes/cliente-1/comprobantes/factura-1/comprobante.html?layout=oficial",
        text="<html>...</html>",
    )
    assert await client.get_comprobante_html("cliente-1", "factura-1") == "<html>...</html>"


async def test_get_comprobante_pdf_layout_explicito(client, httpx_mock):
    httpx_mock.add_response(
        method="GET",
        url=f"{_API}/clientes/cliente-1/comprobantes/factura-1/comprobante.pdf?layout=simplificada",
        content=b"%PDF-1.4...",
        headers={"Content-Type": "application/pdf"},
    )
    assert (
        await client.get_comprobante_pdf("cliente-1", "factura-1", layout="simplificada")
        == b"%PDF-1.4..."
    )


async def test_get_comprobante_imagen(client, httpx_mock):
    httpx_mock.add_response(
        method="GET",
        url=f"{_API}/clientes/cliente-1/comprobantes/factura-1/comprobante.imagen?layout=oficial",
        content=b"\x89PNG...",
    )
    assert await client.get_comprobante_imagen("cliente-1", "factura-1") == b"\x89PNG..."


async def test_get_comprobante_pdf_503_levanta_servicio_no_disponible_error(client, httpx_mock):
    httpx_mock.add_response(
        method="GET",
        url=f"{_API}/clientes/cliente-1/comprobantes/factura-1/comprobante.pdf?layout=oficial",
        status_code=503,
        json=_error("interno", "servicio_no_disponible", "El renderizador no está disponible."),
    )
    with pytest.raises(ServicioNoDisponibleError) as exc_info:
        await client.get_comprobante_pdf("cliente-1", "factura-1")
    assert isinstance(exc_info.value, InternoError)


# ---------------------------------------------------------------------------
# Vista embebible (iframe)
# ---------------------------------------------------------------------------


async def test_crear_embed_token(client, httpx_mock):
    httpx_mock.add_response(
        method="POST",
        url=f"{_API}/clientes/cliente-1/comprobantes/factura-1/embed-token",
        json={
            "embed_url": "https://arca.test/embed/comprobantes/xyz/comprobante.html",
            "expires_at": "2026-08-21T22:30:00.000000Z",
        },
    )
    result = await client.crear_embed_token("cliente-1", "factura-1")
    assert result.embed_url == "https://arca.test/embed/comprobantes/xyz/comprobante.html"
    assert result.expires_at == datetime(2026, 8, 21, 22, 30, tzinfo=timezone.utc)


async def test_crear_embed_token_idempotency_key_ajena_levanta_not_found_error(client, httpx_mock):
    httpx_mock.add_response(
        method="POST",
        url=f"{_API}/clientes/cliente-1/comprobantes/no-existe/embed-token",
        status_code=404,
        json=_error("request", "no_encontrado", "No encontrado."),
    )
    with pytest.raises(NotFoundError):
        await client.crear_embed_token("cliente-1", "no-existe")


async def test_crear_conexion_afip_embed_token(client, httpx_mock):
    httpx_mock.add_response(
        method="POST",
        url=f"{_API}/clientes/cliente-1/conexion-afip/embed-token",
        json={
            "embed_url": "https://arca.test/embed/conexion-afip/xyz",
            "expires_at": "2026-08-23T22:30:00.000000Z",
        },
    )
    result = await client.crear_conexion_afip_embed_token("cliente-1")
    assert result.embed_url == "https://arca.test/embed/conexion-afip/xyz"
    assert result.expires_at == datetime(2026, 8, 23, 22, 30, tzinfo=timezone.utc)


async def test_crear_conexion_afip_embed_token_external_ref_ajeno_levanta_not_found_error(
    client, httpx_mock
):
    httpx_mock.add_response(
        method="POST",
        url=f"{_API}/clientes/no-existe/conexion-afip/embed-token",
        status_code=404,
        json=_error("request", "no_encontrado", "No encontrado."),
    )
    with pytest.raises(NotFoundError):
        await client.crear_conexion_afip_embed_token("no-existe")


# ---------------------------------------------------------------------------
# Lote
# ---------------------------------------------------------------------------


async def test_emitir_lote_comprobantes_manda_la_clave_comprobantes(client, httpx_mock):
    capturado = {}

    def _responder(request):
        import json as _json

        capturado["body"] = _json.loads(request.content)
        return __import__("httpx").Response(
            200,
            json=[
                {
                    "idempotency_key": "lote-1",
                    "ok": True,
                    "emision": {
                        "id": "x",
                        "idempotency_key": "lote-1",
                        "tipo": "FACTURA",
                        "estado": "pending",
                    },
                },
                {
                    "idempotency_key": "lote-2",
                    "ok": False,
                    "error": "Ya existe un intento con esta idempotency_key pero con datos distintos",
                    "status_code": 409,
                },
            ],
        )

    httpx_mock.add_callback(
        _responder, method="POST", url=f"{_API}/clientes/cliente-1/comprobantes/lote"
    )

    resultados = await client.emitir_lote_comprobantes(
        "cliente-1",
        [_comprobante(idempotency_key="lote-1"), _comprobante(idempotency_key="lote-2")],
    )

    assert capturado["body"]["comprobantes"][0]["idempotency_key"] == "lote-1"
    assert len(capturado["body"]["comprobantes"]) == 2

    assert resultados[0].ok is True
    assert resultados[0].emision.idempotency_key == "lote-1"
    assert resultados[1].ok is False
    assert resultados[1].status_code == 409
    assert resultados[1].emision is None


async def test_emitir_lote_notas_credito_manda_la_clave_notas_credito(client, httpx_mock):
    httpx_mock.add_response(
        method="POST",
        url=f"{_API}/clientes/cliente-1/notas-credito/lote",
        json=[
            {
                "idempotency_key": "nc-1",
                "ok": True,
                "emision": {
                    "id": "x",
                    "idempotency_key": "nc-1",
                    "tipo": "NOTA_CREDITO",
                    "estado": "pending",
                },
            }
        ],
    )
    nota = _comprobante(
        idempotency_key="nc-1",
        comprobante_asociado=ComprobanteAsociado(tipo=1, punto_venta=3, numero=100),
    )
    resultados = await client.emitir_lote_notas_credito("cliente-1", [nota])
    assert resultados[0].emision.tipo == "NOTA_CREDITO"


async def test_emitir_lote_notas_debito_manda_la_clave_notas_debito(client, httpx_mock):
    httpx_mock.add_response(
        method="POST",
        url=f"{_API}/clientes/cliente-1/notas-debito/lote",
        json=[
            {
                "idempotency_key": "nd-1",
                "ok": True,
                "emision": {
                    "id": "x",
                    "idempotency_key": "nd-1",
                    "tipo": "NOTA_DEBITO",
                    "estado": "pending",
                },
            }
        ],
    )
    nota = _comprobante(
        idempotency_key="nd-1",
        comprobante_asociado=ComprobanteAsociado(tipo=1, punto_venta=3, numero=100),
    )
    resultados = await client.emitir_lote_notas_debito("cliente-1", [nota])
    assert resultados[0].emision.tipo == "NOTA_DEBITO"


async def test_emitir_lote_comprobantes_mas_de_200_items_levanta_request_error(client, httpx_mock):
    # El lote entero (no un ítem puntual) SÍ puede fallar — ahí el 422 real
    # de _raise_for_status aplica, distinto del ok:false por-ítem de arriba.
    httpx_mock.add_response(
        method="POST",
        url=f"{_API}/clientes/cliente-1/comprobantes/lote",
        status_code=422,
        json=_error(
            "request", "campo_invalido", "El lote admite hasta 200 items (recibidos: 201)."
        ),
    )
    with pytest.raises(RequestError, match="200 items"):
        await client.emitir_lote_comprobantes("cliente-1", [_comprobante()])


# ---------------------------------------------------------------------------
# Webhooks
# ---------------------------------------------------------------------------


async def test_reenviar_webhook(client, httpx_mock):
    httpx_mock.add_response(
        method="POST",
        url=f"{_API}/clientes/cliente-1/comprobantes/factura-1/webhook/reenviar",
        status_code=202,
        json={
            "id": "x",
            "idempotency_key": "factura-1",
            "tipo": "FACTURA",
            "estado": "issued",
            "webhook_delivered": True,
        },
    )
    result = await client.reenviar_webhook("cliente-1", "factura-1")
    assert result.webhook_delivered is True


# ---------------------------------------------------------------------------
# Sobre de error: {"error": {"type", "code", "message", "param"?, "afip"?}}
# ---------------------------------------------------------------------------


async def test_error_expone_type_code_message_param(client, httpx_mock):
    httpx_mock.add_response(
        method="POST",
        url=f"{_API}/clientes/cliente-1/comprobantes",
        status_code=422,
        json=_error("request", "campo_invalido", "items: can't be blank", param="items"),
    )
    with pytest.raises(RequestError) as exc_info:
        await client.emitir_comprobante("cliente-1", _comprobante())
    assert exc_info.value.type == "request"
    assert exc_info.value.code == "campo_invalido"
    assert exc_info.value.message == "items: can't be blank"
    assert exc_info.value.param == "items"


async def test_code_desconocido_cae_en_la_excepcion_de_su_type(client, httpx_mock):
    httpx_mock.add_response(
        method="POST",
        url=f"{_API}/clientes/cliente-1/comprobantes",
        status_code=422,
        json=_error("configuracion", "un_code_que_todavia_no_existe", "Trámite pendiente."),
    )
    with pytest.raises(ConfiguracionError) as exc_info:
        await client.emitir_comprobante("cliente-1", _comprobante())
    assert exc_info.value.code == "un_code_que_todavia_no_existe"
    assert not isinstance(exc_info.value, PuntoVentaNoHabilitadoError)


async def test_punto_venta_no_habilitado_levanta_configuracion_error(client, httpx_mock):
    httpx_mock.add_response(
        method="POST",
        url=f"{_API}/clientes/cliente-1/comprobantes",
        status_code=422,
        json=_error(
            "configuracion",
            "punto_venta_no_habilitado",
            "El punto de venta 3 no está habilitado en AFIP.",
        ),
    )
    with pytest.raises(PuntoVentaNoHabilitadoError) as exc_info:
        await client.emitir_comprobante("cliente-1", _comprobante())
    assert isinstance(exc_info.value, ConfiguracionError)


async def test_nota_excede_comprobante_levanta_request_error_con_param(client, httpx_mock):
    httpx_mock.add_response(
        method="POST",
        url=f"{_API}/clientes/cliente-1/notas-credito",
        status_code=422,
        json=_error(
            "request",
            "nota_excede_comprobante",
            "La nota de crédito excede el saldo disponible ($500.00).",
            param="comprobante_asociado",
        ),
    )
    nota = _comprobante(
        idempotency_key="nc-1",
        comprobante_asociado=ComprobanteAsociado(tipo=1, punto_venta=3, numero=100),
    )
    with pytest.raises(NotaExcedeComprobanteError) as exc_info:
        await client.emitir_nota_credito("cliente-1", nota)
    assert exc_info.value.param == "comprobante_asociado"


async def test_afip_rechazo_expone_los_codigos_de_afip_sin_masticar(client, httpx_mock):
    httpx_mock.add_response(
        method="POST",
        url=f"{_API}/clientes/cliente-1/comprobantes",
        status_code=422,
        json=_error(
            "afip",
            "afip_rechazo",
            "10016: La fecha del comprobante está fuera de rango",
            afip=[{"codigo": 10016, "mensaje": "La fecha del comprobante está fuera de rango"}],
        ),
    )
    with pytest.raises(AfipRechazoError) as exc_info:
        await client.emitir_comprobante("cliente-1", _comprobante())
    assert isinstance(exc_info.value, AfipError)
    assert exc_info.value.afip == (
        AfipErrorDetail(codigo=10016, mensaje="La fecha del comprobante está fuera de rango"),
    )


async def test_409_levanta_idempotency_conflict_error(client, httpx_mock):
    httpx_mock.add_response(
        method="POST",
        url=f"{_API}/clientes/cliente-1/comprobantes",
        status_code=409,
        json=_error(
            "request",
            "idempotency_key_reusada",
            "Ya existe un intento con esta idempotency_key pero con datos distintos",
            param="idempotency_key",
        ),
    )
    with pytest.raises(IdempotencyConflictError):
        await client.emitir_comprobante("cliente-1", _comprobante())


async def test_404_levanta_not_found_error(client, httpx_mock):
    httpx_mock.add_response(
        method="GET",
        url=f"{_API}/clientes/cliente-1/comprobantes/no-existe",
        status_code=404,
        json=_error("request", "no_encontrado", "Not Found"),
    )
    with pytest.raises(NotFoundError) as exc_info:
        await client.get_comprobante("cliente-1", "no-existe")
    assert exc_info.value.status_code == 404
    assert exc_info.value.message == "Not Found"


async def test_429_levanta_rate_limited_error_con_retry_after(client, httpx_mock):
    httpx_mock.add_response(
        method="POST",
        url=f"{_API}/clientes/cliente-1/comprobantes",
        status_code=429,
        headers={"Retry-After": "7"},
        json=_error("request", "rate_limit", "Demasiados requests — respetá Retry-After."),
    )
    with pytest.raises(RateLimitedError) as exc_info:
        await client.emitir_comprobante("cliente-1", _comprobante())
    assert exc_info.value.retry_after == 7


async def test_429_sin_retry_after_header_deja_retry_after_none(client, httpx_mock):
    httpx_mock.add_response(
        method="POST",
        url=f"{_API}/clientes/cliente-1/comprobantes",
        status_code=429,
        json=_error("request", "rate_limit", "..."),
    )
    with pytest.raises(RateLimitedError) as exc_info:
        await client.emitir_comprobante("cliente-1", _comprobante())
    assert exc_info.value.retry_after is None


async def test_502_levanta_afip_unavailable_error(client, httpx_mock):
    httpx_mock.add_response(
        method="GET",
        url=f"{_API}/clientes/cliente-1/credencial/puntos-venta",
        status_code=502,
        json=_error("afip", "afip_sin_respuesta", "AFIP no respondió: timeout"),
    )
    with pytest.raises(AfipUnavailableError):
        await client.listar_puntos_de_venta("cliente-1")


async def test_503_levanta_servicio_no_disponible_error(client, httpx_mock):
    httpx_mock.add_response(
        method="GET",
        url=f"{_API}/envelope/clave-publica",
        status_code=503,
        json=_error(
            "interno",
            "servicio_no_disponible",
            "El servicio todavía no tiene un par de claves de envelope configurado.",
        ),
    )
    with pytest.raises(ServicioNoDisponibleError):
        await client.importar_credencial("cliente-1", "20301234563", "cert", "key")


async def test_500_levanta_interno_error(client, httpx_mock):
    httpx_mock.add_response(
        method="GET",
        url=f"{_API}/clientes/cliente-1/comprobantes/factura-1",
        status_code=500,
        json=_error("interno", "error_interno", "Error interno del servicio."),
    )
    with pytest.raises(InternoError):
        await client.get_comprobante("cliente-1", "factura-1")


async def test_error_sin_sobre_json_no_rompe_el_parseo(client, httpx_mock):
    httpx_mock.add_response(
        method="GET",
        url=f"{_API}/clientes/cliente-1/comprobantes/factura-1",
        status_code=502,
        html="<html>Bad Gateway</html>",
    )
    with pytest.raises(InternoError) as exc_info:
        await client.get_comprobante("cliente-1", "factura-1")
    assert "Bad Gateway" in exc_info.value.message


def _clave_publica_de_test() -> str:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return (
        key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode()
    )
