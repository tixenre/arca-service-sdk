"""Tests de ArcaServiceClient — sin red real (`pytest_httpx` mockea el transporte de
httpx). El certificado mTLS SÍ es real pero descartable (`client_cert_files`, ver
conftest.py): `httpx.Client(cert=...)` carga los archivos al construirse, incluso con el
transporte mockeado después.

Cada test de método verifica DOS cosas por separado: qué se manda (URL/método/body,
contra el router/controllers de Phoenix reales,
`lib/arca_service_phx_web/router.ex`/`controllers/*.ex`) y cómo se parsea la respuesta
(contra `lib/arca_service_phx_web/schemas/*.ex` real) — no alcanza con que uno de los dos
ande."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from arca_service_client import (
    AfipUnavailableError,
    ArcaServiceClient,
    ArcaServiceServerError,
    BonificadoLimiteError,
    ComprobanteAsociado,
    ComprobanteInput,
    IdempotencyConflictError,
    NotFoundError,
    RateLimitedError,
    ServiceNotReadyError,
    ValidationError,
)

_BASE_URL = "https://arca.test"
_API = "https://arca.test/api/v1"


@pytest.fixture
def client(client_cert_files):
    cert_path, key_path = client_cert_files
    c = ArcaServiceClient(
        base_url=_BASE_URL,
        client_cert_path=cert_path,
        client_key_path=key_path,
        api_key="test-api-key",
    )
    yield c
    c.close()


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


# ---------------------------------------------------------------------------
# base_url / auth
# ---------------------------------------------------------------------------


def test_base_url_agrega_prefijo_api_v1_y_saca_barra_final(client_cert_files):
    cert_path, key_path = client_cert_files
    c = ArcaServiceClient(
        base_url="https://arca.test/",  # con barra final
        client_cert_path=cert_path,
        client_key_path=key_path,
        api_key="x",
    )
    # httpx normaliza un base_url a terminar en "/" internamente (para poder resolver
    # paths relativos) — lo que importa es que el prefijo /api/v1 esté una sola vez,
    # sin doble barra ni duplicado; eso lo confirman el resto de los tests, que
    # verifican la URL COMPLETA de cada request.
    assert str(c._http.base_url) == "https://arca.test/api/v1/"
    c.close()


def test_manda_authorization_bearer_con_la_api_key(client, httpx_mock):
    httpx_mock.add_response(
        method="GET",
        url=f"{_API}/clientes/cliente-1/comprobantes/factura-1",
        match_headers={"Authorization": "Bearer test-api-key"},
        json={"id": "x", "idempotency_key": "factura-1", "tipo": "FACTURA", "estado": "pending"},
    )
    client.get_comprobante("cliente-1", "factura-1")


def test_context_manager_cierra_el_cliente_http(client_cert_files):
    cert_path, key_path = client_cert_files
    with ArcaServiceClient(
        base_url=_BASE_URL, client_cert_path=cert_path, client_key_path=key_path, api_key="x"
    ) as c:
        assert not c._http.is_closed
    assert c._http.is_closed


# ---------------------------------------------------------------------------
# Cliente — onboarding por CUIT + vínculo (Fase 12)
# ---------------------------------------------------------------------------


def test_por_cuit(client, httpx_mock):
    httpx_mock.add_response(
        method="POST",
        url=f"{_API}/clientes/por-cuit",
        match_json={"cuit": "20301234563"},
        json={"external_ref": "cliente-1"},
    )
    result = client.por_cuit("20301234563")
    assert result.external_ref == "cliente-1"


def test_por_cuit_cuit_invalido_levanta_validation_error(client, httpx_mock):
    httpx_mock.add_response(
        method="POST",
        url=f"{_API}/clientes/por-cuit",
        status_code=422,
        json={"detail": "'123' no es un CUIT/CUIL válido."},
    )
    with pytest.raises(ValidationError, match="CUIT/CUIL válido"):
        client.por_cuit("123")


def test_set_bonificado_activar(client, httpx_mock):
    httpx_mock.add_response(
        method="PUT",
        url=f"{_API}/clientes/cliente-1/bonificado",
        match_json={"bonificado": True},
        json={"bonificado": True},
    )
    result = client.set_bonificado("cliente-1", True)
    assert result.bonificado is True


def test_set_bonificado_desactivar(client, httpx_mock):
    httpx_mock.add_response(
        method="PUT",
        url=f"{_API}/clientes/cliente-1/bonificado",
        match_json={"bonificado": False},
        json={"bonificado": False},
    )
    result = client.set_bonificado("cliente-1", False)
    assert result.bonificado is False


def test_set_bonificado_409_levanta_bonificado_limite_error_no_idempotency_conflict(
    client, httpx_mock
):
    """El 409 de `set_bonificado` es un tipo DISTINTO al de idempotencia (ver
    `BonificadoLimiteError` en exceptions.py) -- este test existe específicamente para
    que un futuro cambio no lo confunda con `IdempotencyConflictError` sin que nada lo
    note (los dos comparten status_code, solo el tipo distingue)."""
    httpx_mock.add_response(
        method="PUT",
        url=f"{_API}/clientes/cliente-1/bonificado",
        status_code=409,
        json={"detail": "Se alcanzó el límite de seguridad de bonificados para esta plataforma."},
    )
    with pytest.raises(BonificadoLimiteError) as exc_info:
        client.set_bonificado("cliente-1", True)
    assert not isinstance(exc_info.value, IdempotencyConflictError)
    assert exc_info.value.status_code == 409


def test_set_bonificado_404_cliente_no_vinculado_levanta_not_found_error(client, httpx_mock):
    httpx_mock.add_response(
        method="PUT",
        url=f"{_API}/clientes/cliente-1/bonificado",
        status_code=404,
        json={"detail": "Cliente no encontrado."},
    )
    with pytest.raises(NotFoundError):
        client.set_bonificado("cliente-1", True)


# ---------------------------------------------------------------------------
# Onboarding de credencial
# ---------------------------------------------------------------------------


def test_generar_csr(client, httpx_mock):
    httpx_mock.add_response(
        method="POST",
        url=f"{_API}/clientes/cliente-1/csr",
        match_json={"cuit": "20301234563", "regenerar": False},
        json={"csr_pem": "-----BEGIN CERTIFICATE REQUEST-----...", "alias": "cliente-1-2026"},
    )
    result = client.generar_csr("cliente-1", "20301234563")
    assert result.alias == "cliente-1-2026"


def test_completar_credencial(client, httpx_mock):
    httpx_mock.add_response(
        method="POST",
        url=f"{_API}/clientes/cliente-1/credencial/completar",
        match_json={"cert_pem": "-----BEGIN CERTIFICATE-----...", "point_of_sale": 3},
        json={"point_of_sale": 3, "active": True},
    )
    result = client.completar_credencial(
        "cliente-1", "-----BEGIN CERTIFICATE-----...", point_of_sale=3
    )
    assert result.point_of_sale == 3
    assert result.active is True


def test_importar_credencial_pide_clave_publica_y_sella_antes_de_mandar(client, httpx_mock):
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

    result = client.importar_credencial(
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


def test_diagnosticar_credencial(client, httpx_mock):
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
    result = client.diagnosticar_credencial("cliente-1")
    assert result.listo is True
    assert result.chequeos[0].check == "cert_vigente"


def test_listar_puntos_de_venta(client, httpx_mock):
    httpx_mock.add_response(
        method="GET",
        url=f"{_API}/clientes/cliente-1/credencial/puntos-venta",
        json={
            "habilitados": [{"nro": 3, "emision_tipo": "CAE"}],
            "excluidos": [{"nro": 4, "motivo": "sin factura electrónica habilitada"}],
        },
    )
    result = client.listar_puntos_de_venta("cliente-1")
    assert result.habilitados[0].nro == 3
    assert result.excluidos[0].motivo == "sin factura electrónica habilitada"


# ---------------------------------------------------------------------------
# Padrón
# ---------------------------------------------------------------------------


def test_consultar_padron(client, httpx_mock):
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
    persona = client.consultar_padron("cliente-1", "20301234563")
    assert persona.razon_social == "Acme"


# ---------------------------------------------------------------------------
# Preview / emisión
# ---------------------------------------------------------------------------


def test_preview_comprobante(client, httpx_mock):
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
    result = client.preview_comprobante("cliente-1", _comprobante())
    assert result.cbte_letra == "B"
    assert result.importe_total == Decimal("1210.00")


def test_emitir_comprobante_devuelve_pending(client, httpx_mock):
    httpx_mock.add_response(
        method="POST",
        url=f"{_API}/clientes/cliente-1/comprobantes",
        status_code=202,
        json={"id": "x", "idempotency_key": "factura-1", "tipo": "FACTURA", "estado": "pending"},
    )
    result = client.emitir_comprobante("cliente-1", _comprobante())
    assert result.estado == "pending"


def test_emitir_nota_credito_manda_al_endpoint_de_notas_credito(client, httpx_mock):
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
    result = client.emitir_nota_credito("cliente-1", nota)
    assert result.tipo == "NOTA_CREDITO"


def test_preview_nota_debito(client, httpx_mock):
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
    result = client.preview_nota_debito("cliente-1", nota)
    assert result.cbte_letra == "B"


def test_emitir_nota_debito_manda_al_endpoint_de_notas_debito(client, httpx_mock):
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
    result = client.emitir_nota_debito("cliente-1", nota)
    assert result.tipo == "NOTA_DEBITO"


def test_get_comprobante_issued(client, httpx_mock):
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
    result = client.get_comprobante("cliente-1", "factura-1")
    assert result.numero == 42
    assert result.cae == "71234567890123"


# ---------------------------------------------------------------------------
# Documento renderizado
# ---------------------------------------------------------------------------


def test_get_comprobante_html_manda_layout_default_oficial(client, httpx_mock):
    httpx_mock.add_response(
        method="GET",
        url=f"{_API}/clientes/cliente-1/comprobantes/factura-1/comprobante.html?layout=oficial",
        text="<html>...</html>",
    )
    assert client.get_comprobante_html("cliente-1", "factura-1") == "<html>...</html>"


def test_get_comprobante_pdf_layout_explicito(client, httpx_mock):
    httpx_mock.add_response(
        method="GET",
        url=f"{_API}/clientes/cliente-1/comprobantes/factura-1/comprobante.pdf?layout=simplificada",
        content=b"%PDF-1.4...",
        headers={"Content-Type": "application/pdf"},
    )
    assert (
        client.get_comprobante_pdf("cliente-1", "factura-1", layout="simplificada")
        == b"%PDF-1.4..."
    )


def test_get_comprobante_imagen(client, httpx_mock):
    httpx_mock.add_response(
        method="GET",
        url=f"{_API}/clientes/cliente-1/comprobantes/factura-1/comprobante.imagen?layout=oficial",
        content=b"\x89PNG...",
    )
    assert client.get_comprobante_imagen("cliente-1", "factura-1") == b"\x89PNG..."


# ---------------------------------------------------------------------------
# Lote
# ---------------------------------------------------------------------------


def test_emitir_lote_comprobantes_manda_la_clave_comprobantes(client, httpx_mock):
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

    resultados = client.emitir_lote_comprobantes(
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


def test_emitir_lote_notas_credito_manda_la_clave_notas_credito(client, httpx_mock):
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
    resultados = client.emitir_lote_notas_credito("cliente-1", [nota])
    assert resultados[0].emision.tipo == "NOTA_CREDITO"


def test_emitir_lote_notas_debito_manda_la_clave_notas_debito(client, httpx_mock):
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
    resultados = client.emitir_lote_notas_debito("cliente-1", [nota])
    assert resultados[0].emision.tipo == "NOTA_DEBITO"


def test_emitir_lote_comprobantes_mas_de_200_items_levanta_validation_error(client, httpx_mock):
    # El lote entero (no un ítem puntual) SÍ puede fallar — ahí el 422 real
    # de _raise_for_status aplica, distinto del ok:false por-ítem de arriba.
    httpx_mock.add_response(
        method="POST",
        url=f"{_API}/clientes/cliente-1/comprobantes/lote",
        status_code=422,
        json={"detail": "El lote admite hasta 200 items (recibidos: 201)."},
    )
    with pytest.raises(ValidationError, match="200 items"):
        client.emitir_lote_comprobantes("cliente-1", [_comprobante()])


# ---------------------------------------------------------------------------
# Webhooks
# ---------------------------------------------------------------------------


def test_reenviar_webhook(client, httpx_mock):
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
    result = client.reenviar_webhook("cliente-1", "factura-1")
    assert result.webhook_delivered is True


# ---------------------------------------------------------------------------
# Mapeo de errores por status code
# ---------------------------------------------------------------------------


def test_404_levanta_not_found_error(client, httpx_mock):
    httpx_mock.add_response(
        method="GET",
        url=f"{_API}/clientes/cliente-1/comprobantes/no-existe",
        status_code=404,
        json={"detail": "Not Found"},
    )
    with pytest.raises(NotFoundError) as exc_info:
        client.get_comprobante("cliente-1", "no-existe")
    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Not Found"


def test_409_levanta_idempotency_conflict_error(client, httpx_mock):
    httpx_mock.add_response(
        method="POST",
        url=f"{_API}/clientes/cliente-1/comprobantes",
        status_code=409,
        json={"detail": "Ya existe un intento con esta idempotency_key pero con datos distintos"},
    )
    with pytest.raises(IdempotencyConflictError):
        client.emitir_comprobante("cliente-1", _comprobante())


def test_422_levanta_validation_error(client, httpx_mock):
    httpx_mock.add_response(
        method="POST",
        url=f"{_API}/clientes/cliente-1/comprobantes",
        status_code=422,
        json={"detail": "'99999999999' no es un CUIT válido."},
    )
    with pytest.raises(ValidationError, match="CUIT válido"):
        client.emitir_comprobante("cliente-1", _comprobante())


def test_429_levanta_rate_limited_error_con_retry_after(client, httpx_mock):
    httpx_mock.add_response(
        method="POST",
        url=f"{_API}/clientes/cliente-1/comprobantes",
        status_code=429,
        headers={"Retry-After": "7"},
        json={"detail": "Demasiados requests — respetá Retry-After antes de reintentar."},
    )
    with pytest.raises(RateLimitedError) as exc_info:
        client.emitir_comprobante("cliente-1", _comprobante())
    assert exc_info.value.retry_after == 7


def test_429_sin_retry_after_header_deja_retry_after_none(client, httpx_mock):
    httpx_mock.add_response(
        method="POST",
        url=f"{_API}/clientes/cliente-1/comprobantes",
        status_code=429,
        json={"detail": "..."},
    )
    with pytest.raises(RateLimitedError) as exc_info:
        client.emitir_comprobante("cliente-1", _comprobante())
    assert exc_info.value.retry_after is None


def test_502_levanta_afip_unavailable_error(client, httpx_mock):
    httpx_mock.add_response(
        method="GET",
        url=f"{_API}/clientes/cliente-1/credencial/puntos-venta",
        status_code=502,
        json={"detail": "AFIP no respondió: timeout"},
    )
    with pytest.raises(AfipUnavailableError):
        client.listar_puntos_de_venta("cliente-1")


def test_503_levanta_service_not_ready_error(client, httpx_mock):
    httpx_mock.add_response(
        method="GET",
        url=f"{_API}/envelope/clave-publica",
        status_code=503,
        json={"detail": "El servicio todavía no tiene un par de claves de envelope configurado."},
    )
    with pytest.raises(ServiceNotReadyError):
        client.importar_credencial("cliente-1", "20301234563", "cert", "key")


def test_500_levanta_arca_service_server_error(client, httpx_mock):
    httpx_mock.add_response(
        method="GET",
        url=f"{_API}/clientes/cliente-1/comprobantes/factura-1",
        status_code=500,
        json={"detail": "Error interno del servicio."},
    )
    with pytest.raises(ArcaServiceServerError):
        client.get_comprobante("cliente-1", "factura-1")


def test_error_sin_body_json_no_rompe_el_parseo_de_detail(client, httpx_mock):
    """Si el error NO viene como `{"detail": "..."}` (ej. un proxy intermedio devolviendo
    HTML de error), `_extraer_detail` no debe levantar un error DISTINTO que oculte el
    original."""
    httpx_mock.add_response(
        method="GET",
        url=f"{_API}/clientes/cliente-1/comprobantes/factura-1",
        status_code=502,
        html="<html>Bad Gateway</html>",
    )
    with pytest.raises(AfipUnavailableError) as exc_info:
        client.get_comprobante("cliente-1", "factura-1")
    assert "Bad Gateway" in exc_info.value.detail


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
