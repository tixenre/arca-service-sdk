"""Tests de ArcaServiceClient — sin red real (`pytest_httpx` mockea el transporte de
httpx). El certificado mTLS SÍ es real pero descartable (`client_cert_files`, ver
conftest.py): `httpx.Client(cert=...)` carga los archivos al construirse, incluso con el
transporte mockeado después.

Cada test de método verifica DOS cosas por separado: qué se manda (URL/método/body,
contra el contrato HTTP real de la API) y cómo se parsea la respuesta (contra el shape
real que devuelve el servidor) — no alcanza con que uno de los dos ande.

`comprobante`/`importes`/`receptor` en las respuestas de emisión/preview son objetos
anidados -- ver `tests/test_contract.py` para los valores verificados contra el
comportamiento real de la API, en vez de repetirlos acá."""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from decimal import Decimal

import httpx
import pytest

from arca_service_client import (
    AfipError,
    AfipErrorDetail,
    AfipRechazoError,
    AfipUnavailableError,
    ArcaServiceClient,
    BonificadoLimiteError,
    ComprobanteAsociado,
    ComprobanteInput,
    ConfiguracionError,
    IdempotencyConflictError,
    InternoError,
    ItemFactura,
    NotaExcedeComprobanteError,
    NotFoundError,
    PuntoVentaNoHabilitadoError,
    RateLimitedError,
    Receptor,
    RequestError,
    ServicioNoDisponibleError,
    SesionEmbebidaInput,
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
        receptor=Receptor(dni="12345678"),
        fecha=date(2026, 8, 18),
        items=[
            ItemFactura(descripcion="Plan mensual", iva="21", precio_unitario=Decimal("1000.00"))
        ],
    )
    kwargs.update(overrides)
    return ComprobanteInput(**kwargs)


def _sesion_embebida(**overrides):
    kwargs = dict(
        idempotency_key="factura-1",
        concepto=1,
        fecha=date(2026, 8, 18),
        items=[
            ItemFactura(descripcion="Plan mensual", iva="21", precio_unitario=Decimal("1000.00"))
        ],
    )
    kwargs.update(overrides)
    return SesionEmbebidaInput(**kwargs)


def _error(type: str, code: str, message: str, **extra):
    """Arma `{"error": {...}}` -- el sobre único de toda la API desde esta migración
    (ver MIGRACION.md, punto 1). `**extra` para `param`/`afip` cuando aplican."""
    return {"error": {"type": type, "code": code, "message": message, **extra}}


def _comprobante_json(**overrides):
    """`comprobante` de una respuesta de emisión/preview. `letra`/`codigo_afip`/
    `punto_venta`/`numero` en `None` por default, como una emisión recién creada
    (`pending`)."""
    d = {
        "tipo": "FACTURA",
        "letra": None,
        "codigo_afip": None,
        "punto_venta": None,
        "numero": None,
        "fecha": "2026-08-18",
    }
    d.update(overrides)
    return d


def _importes_json(**overrides):
    """`importes` de una emisión real. Un preview no trae `moneda`/`cotizacion`; para
    esos tests, pasá `moneda=None, cotizacion=None` explícito o construí el dict a
    mano."""
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
    """`receptor` de una emisión real. `doc_nro` viaja como número, no como string."""
    d = {
        "doc_tipo": {"codigo": 96, "descripcion": "DNI"},
        "doc_nro": 12345678,
        "nombre": "",
        "domicilio": "",
        "condicion_iva": {"codigo": 5, "descripcion": "Consumidor Final", "fuente": "padron"},
    }
    d.update(overrides)
    return d


def _emision_json(**overrides):
    """Una respuesta completa de una emisión. Default `estado="pending"` recién
    creada."""
    d = {
        "id": "x",
        "idempotency_key": "factura-1",
        "estado": "pending",
        "comprobante": _comprobante_json(),
        "importes": _importes_json(),
        "receptor": _receptor_json(),
        "cae": "",
        "cae_vencimiento": None,
        "qr_url": "",
        "errores": None,
        "observaciones": None,
        "webhook_delivered": None,
        "webhook_last_error": "",
    }
    d.update(overrides)
    return d


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
        json=_emision_json(),
    )
    client.get_comprobante("cliente-1", "factura-1")


def test_manda_accept_application_json(client, httpx_mock):
    """Sin este header, un 404 de ruta inexistente vuelve como texto plano ("Not
    Found") en vez del sobre `{"error": {...}}`, y `_parse_error_envelope` se queda sin
    nada que parsear -- comprobado contra producción (`arca.mancino.dev`), no una
    suposición. Este cliente lo manda SIEMPRE, no solo cuando espera un error."""
    httpx_mock.add_response(
        method="GET",
        url=f"{_API}/clientes/cliente-1/comprobantes/factura-1",
        match_headers={"Accept": "application/json"},
        json=_emision_json(),
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
# Cliente — onboarding por CUIT + vínculo
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


def test_por_cuit_cuit_invalido_levanta_request_error(client, httpx_mock):
    httpx_mock.add_response(
        method="POST",
        url=f"{_API}/clientes/por-cuit",
        status_code=422,
        json=_error("request", "campo_invalido", "'123' no es un CUIT/CUIL válido.", param="cuit"),
    )
    with pytest.raises(RequestError, match="CUIT/CUIL válido") as exc_info:
        client.por_cuit("123")
    assert exc_info.value.code == "campo_invalido"
    assert exc_info.value.param == "cuit"


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
    note (los dos comparten status_code, y hoy arca-service ni siquiera los distingue
    por `code` -- los dos traen `idempotency_key_reusada`, ver el comentario en
    `_raise_for_status` -- así que la única señal real es el SITIO DE LLAMADA)."""
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
        client.set_bonificado("cliente-1", True)
    assert not isinstance(exc_info.value, IdempotencyConflictError)
    assert exc_info.value.status_code == 409


def test_set_bonificado_404_cliente_no_vinculado_levanta_not_found_error(client, httpx_mock):
    httpx_mock.add_response(
        method="PUT",
        url=f"{_API}/clientes/cliente-1/bonificado",
        status_code=404,
        json=_error("request", "no_encontrado", "Cliente no encontrado."),
    )
    with pytest.raises(NotFoundError):
        client.set_bonificado("cliente-1", True)


def test_set_facturacion_manda_los_dos_campos(client, httpx_mock):
    httpx_mock.add_response(
        method="PUT",
        url=f"{_API}/clientes/cliente-1/facturacion",
        match_json={"iibb": "901-123456-7", "nombre_comercial": "La Esquina"},
        json={"iibb": "901-123456-7", "nombre_comercial": "La Esquina"},
    )
    result = client.set_facturacion("cliente-1", iibb="901-123456-7", nombre_comercial="La Esquina")
    assert result.iibb == "901-123456-7"
    assert result.nombre_comercial == "La Esquina"


def test_set_facturacion_omite_el_campo_no_pasado(client, httpx_mock):
    httpx_mock.add_response(
        method="PUT",
        url=f"{_API}/clientes/cliente-1/facturacion",
        match_json={"iibb": "901-123456-7"},
        json={"iibb": "901-123456-7", "nombre_comercial": ""},
    )
    client.set_facturacion("cliente-1", iibb="901-123456-7")


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
            "comprobante": {"tipo": "factura", "letra": "B", "codigo_afip": 6},
            "importes": _importes_json(total="1210.00", iva="210.00", moneda=None, cotizacion=None),
        },
    )
    result = client.preview_comprobante("cliente-1", _comprobante())
    assert result.comprobante.letra == "B"
    assert result.comprobante.codigo_afip == 6
    assert result.importes.total == Decimal("1210.00")


def test_emitir_comprobante_devuelve_pending(client, httpx_mock):
    httpx_mock.add_response(
        method="POST",
        url=f"{_API}/clientes/cliente-1/comprobantes",
        status_code=202,
        json=_emision_json(),
    )
    result = client.emitir_comprobante("cliente-1", _comprobante())
    assert result.estado == "pending"


def test_emitir_nota_credito_manda_al_endpoint_de_notas_credito(client, httpx_mock):
    httpx_mock.add_response(
        method="POST",
        url=f"{_API}/clientes/cliente-1/notas-credito",
        status_code=202,
        json=_emision_json(
            idempotency_key="nc-1", comprobante=_comprobante_json(tipo="NOTA_CREDITO")
        ),
    )
    nota = _comprobante(
        idempotency_key="nc-1",
        comprobante_asociado=ComprobanteAsociado(tipo=1, punto_venta=3, numero=100),
    )
    result = client.emitir_nota_credito("cliente-1", nota)
    assert result.comprobante.tipo == "NOTA_CREDITO"


def test_preview_nota_debito(client, httpx_mock):
    httpx_mock.add_response(
        method="POST",
        url=f"{_API}/clientes/cliente-1/notas-debito/preview",
        json={
            "comprobante": {"tipo": "nota_debito", "letra": "B", "codigo_afip": 7},
            "importes": _importes_json(total="1210.00", iva="210.00", moneda=None, cotizacion=None),
        },
    )
    nota = _comprobante(
        idempotency_key="nd-1",
        comprobante_asociado=ComprobanteAsociado(tipo=1, punto_venta=3, numero=100),
    )
    result = client.preview_nota_debito("cliente-1", nota)
    assert result.comprobante.letra == "B"


def test_emitir_nota_debito_manda_al_endpoint_de_notas_debito(client, httpx_mock):
    httpx_mock.add_response(
        method="POST",
        url=f"{_API}/clientes/cliente-1/notas-debito",
        status_code=202,
        json=_emision_json(
            idempotency_key="nd-1", comprobante=_comprobante_json(tipo="NOTA_DEBITO")
        ),
    )
    nota = _comprobante(
        idempotency_key="nd-1",
        comprobante_asociado=ComprobanteAsociado(tipo=1, punto_venta=3, numero=100),
    )
    result = client.emitir_nota_debito("cliente-1", nota)
    assert result.comprobante.tipo == "NOTA_DEBITO"


def test_get_comprobante_issued(client, httpx_mock):
    httpx_mock.add_response(
        method="GET",
        url=f"{_API}/clientes/cliente-1/comprobantes/factura-1",
        json=_emision_json(
            estado="issued",
            comprobante=_comprobante_json(letra="B", codigo_afip=6, punto_venta=3, numero=42),
            cae="71234567890123",
        ),
    )
    result = client.get_comprobante("cliente-1", "factura-1")
    assert result.comprobante.numero == 42
    assert result.cae == "71234567890123"


def test_get_comprobante_con_observaciones(client, httpx_mock):
    """`observaciones` -- comentarios de AFIP sobre un comprobante que SÍ autorizó (ver
    MIGRACION.md, punto 4). No son `errores`: la emisión sigue `issued`."""
    httpx_mock.add_response(
        method="GET",
        url=f"{_API}/clientes/cliente-1/comprobantes/factura-1",
        json=_emision_json(
            estado="issued",
            comprobante=_comprobante_json(letra="B", codigo_afip=6, punto_venta=3, numero=42),
            cae="71234567890123",
            observaciones=["10063: el documento del receptor no figura en el padrón"],
        ),
    )
    result = client.get_comprobante("cliente-1", "factura-1")
    assert result.estado == "issued"
    assert result.errores is None
    assert result.observaciones == ["10063: el documento del receptor no figura en el padrón"]


def test_listar_comprobantes_sin_filtros_manda_limit_y_offset_default(client, httpx_mock):
    httpx_mock.add_response(
        method="GET",
        url=f"{_API}/clientes/cliente-1/comprobantes?limit=50&offset=0",
        json={
            "items": [
                _emision_json(idempotency_key="factura-2"),
                _emision_json(idempotency_key="factura-1"),
            ],
            "count": 2,
        },
    )
    result = client.listar_comprobantes("cliente-1")
    assert result.count == 2
    assert [e.idempotency_key for e in result.items] == ["factura-2", "factura-1"]


def test_listar_comprobantes_manda_los_filtros_como_query_params(client, httpx_mock):
    httpx_mock.add_response(
        method="GET",
        url=(
            f"{_API}/clientes/cliente-1/comprobantes"
            "?estado=issued&tipo=factura&creado_desde=2026-08-01&creado_hasta=2026-08-31"
            "&receptor_cuit=20301234563&limit=10&offset=20"
        ),
        json={"items": [], "count": 0},
    )
    result = client.listar_comprobantes(
        "cliente-1",
        estado="issued",
        tipo="factura",
        creado_desde=date(2026, 8, 1),
        creado_hasta=date(2026, 8, 31),
        receptor_cuit="20301234563",
        limit=10,
        offset=20,
    )
    assert result.items == ()
    assert result.count == 0


# ---------------------------------------------------------------------------
# Preview renderizado -- .html/.pdf/.imagen de un preview, antes de emitir.
# ---------------------------------------------------------------------------


def test_preview_comprobante_html_manda_layout_en_el_body(client, httpx_mock):
    capturado = {}

    def _responder(request):
        capturado["body"] = json.loads(request.content)
        return httpx.Response(200, text="<html>VISTA PREVIA...</html>")

    httpx_mock.add_callback(
        _responder,
        method="POST",
        url=f"{_API}/clientes/cliente-1/comprobantes/preview/comprobante.html",
    )
    resultado = client.preview_comprobante_html("cliente-1", _comprobante())
    assert resultado == "<html>VISTA PREVIA...</html>"
    assert capturado["body"]["layout"] == "oficial"
    assert capturado["body"]["idempotency_key"] == "factura-1"


def test_preview_comprobante_pdf_layout_explicito(client, httpx_mock):
    capturado = {}

    def _responder(request):
        capturado["body"] = json.loads(request.content)
        return httpx.Response(
            200, content=b"%PDF-1.4...", headers={"Content-Type": "application/pdf"}
        )

    httpx_mock.add_callback(
        _responder,
        method="POST",
        url=f"{_API}/clientes/cliente-1/comprobantes/preview/comprobante.pdf",
    )
    resultado = client.preview_comprobante_pdf("cliente-1", _comprobante(), layout="simplificada")
    assert resultado == b"%PDF-1.4..."
    assert capturado["body"]["layout"] == "simplificada"


def test_preview_comprobante_imagen(client, httpx_mock):
    httpx_mock.add_response(
        method="POST",
        url=f"{_API}/clientes/cliente-1/comprobantes/preview/comprobante.imagen",
        content=b"\x89PNG...",
    )
    assert client.preview_comprobante_imagen("cliente-1", _comprobante()) == b"\x89PNG..."


def test_preview_nota_credito_html_manda_al_endpoint_de_notas_credito(client, httpx_mock):
    httpx_mock.add_response(
        method="POST",
        url=f"{_API}/clientes/cliente-1/notas-credito/preview/comprobante.html",
        text="<html>...</html>",
    )
    nota = _comprobante(comprobante_asociado=ComprobanteAsociado(tipo=3, punto_venta=3, numero=100))
    assert client.preview_nota_credito_html("cliente-1", nota) == "<html>...</html>"


def test_preview_nota_debito_pdf_manda_al_endpoint_de_notas_debito(client, httpx_mock):
    httpx_mock.add_response(
        method="POST",
        url=f"{_API}/clientes/cliente-1/notas-debito/preview/comprobante.pdf",
        content=b"%PDF-1.4...",
    )
    nota = _comprobante(comprobante_asociado=ComprobanteAsociado(tipo=2, punto_venta=3, numero=100))
    assert client.preview_nota_debito_pdf("cliente-1", nota) == b"%PDF-1.4..."


def test_preview_nota_credito_imagen(client, httpx_mock):
    httpx_mock.add_response(
        method="POST",
        url=f"{_API}/clientes/cliente-1/notas-credito/preview/comprobante.imagen",
        content=b"\x89PNG...",
    )
    nota = _comprobante(comprobante_asociado=ComprobanteAsociado(tipo=3, punto_venta=3, numero=100))
    assert client.preview_nota_credito_imagen("cliente-1", nota) == b"\x89PNG..."


def test_preview_nota_debito_html(client, httpx_mock):
    httpx_mock.add_response(
        method="POST",
        url=f"{_API}/clientes/cliente-1/notas-debito/preview/comprobante.html",
        text="<html>...</html>",
    )
    nota = _comprobante(comprobante_asociado=ComprobanteAsociado(tipo=2, punto_venta=3, numero=100))
    assert client.preview_nota_debito_html("cliente-1", nota) == "<html>...</html>"


def test_preview_nota_credito_pdf(client, httpx_mock):
    httpx_mock.add_response(
        method="POST",
        url=f"{_API}/clientes/cliente-1/notas-credito/preview/comprobante.pdf",
        content=b"%PDF-1.4...",
    )
    nota = _comprobante(comprobante_asociado=ComprobanteAsociado(tipo=3, punto_venta=3, numero=100))
    assert client.preview_nota_credito_pdf("cliente-1", nota) == b"%PDF-1.4..."


def test_preview_nota_debito_imagen(client, httpx_mock):
    httpx_mock.add_response(
        method="POST",
        url=f"{_API}/clientes/cliente-1/notas-debito/preview/comprobante.imagen",
        content=b"\x89PNG...",
    )
    nota = _comprobante(comprobante_asociado=ComprobanteAsociado(tipo=2, punto_venta=3, numero=100))
    assert client.preview_nota_debito_imagen("cliente-1", nota) == b"\x89PNG..."


# ---------------------------------------------------------------------------
# Sesión embebida (iframe) -- puerta de entrada alternativa a emitir_*, sin
# receptor (lo completa el comprador en el iframe). Ver MIGRACION.md, punto 5.
# ---------------------------------------------------------------------------


def test_crear_sesion_embebida_comprobante(client, httpx_mock):
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

    result = client.crear_sesion_embebida_comprobante("cliente-1", _sesion_embebida())

    assert result.embed_url == "https://arca.test/embed/facturar/xyz"
    assert result.expires_at == datetime(2026, 8, 21, 22, 30, tzinfo=timezone.utc)
    # Sin receptor -- eso lo completa el comprador adentro del iframe, no esta llamada.
    assert "receptor" not in capturado["body"]


def test_crear_sesion_embebida_nota_credito_manda_al_endpoint_de_notas_credito(client, httpx_mock):
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
    result = client.crear_sesion_embebida_nota_credito("cliente-1", nota)
    assert result.embed_url == "https://arca.test/embed/facturar/nc-xyz"


def test_crear_sesion_embebida_nota_debito_manda_al_endpoint_de_notas_debito(client, httpx_mock):
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
    result = client.crear_sesion_embebida_nota_debito("cliente-1", nota)
    assert result.embed_url == "https://arca.test/embed/facturar/nd-xyz"


def test_sesion_embebida_input_to_payload_no_tiene_receptor():
    payload = _sesion_embebida().to_payload()
    assert "receptor" not in payload


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


def test_get_comprobante_pdf_503_levanta_servicio_no_disponible_error(client, httpx_mock):
    """El renderizador caído no dice nada sobre si la EMISIÓN falló -- el CAE está, y
    `get_comprobante`/el HTML se pueden seguir pidiendo. Ver MIGRACION.md, punto 2."""
    httpx_mock.add_response(
        method="GET",
        url=f"{_API}/clientes/cliente-1/comprobantes/factura-1/comprobante.pdf?layout=oficial",
        status_code=503,
        json=_error("interno", "servicio_no_disponible", "El renderizador no está disponible."),
    )
    with pytest.raises(ServicioNoDisponibleError) as exc_info:
        client.get_comprobante_pdf("cliente-1", "factura-1")
    assert isinstance(exc_info.value, InternoError)


# ---------------------------------------------------------------------------
# Vista embebible (iframe)
# ---------------------------------------------------------------------------


def test_crear_embed_token(client, httpx_mock):
    httpx_mock.add_response(
        method="POST",
        url=f"{_API}/clientes/cliente-1/comprobantes/factura-1/embed-token",
        json={
            "embed_url": "https://arca.test/embed/comprobantes/xyz/comprobante.html",
            "expires_at": "2026-08-21T22:30:00.000000Z",
        },
    )
    result = client.crear_embed_token("cliente-1", "factura-1")
    assert result.embed_url == "https://arca.test/embed/comprobantes/xyz/comprobante.html"
    assert result.expires_at == datetime(2026, 8, 21, 22, 30, tzinfo=timezone.utc)


def test_crear_embed_token_idempotency_key_ajena_levanta_not_found_error(client, httpx_mock):
    httpx_mock.add_response(
        method="POST",
        url=f"{_API}/clientes/cliente-1/comprobantes/no-existe/embed-token",
        status_code=404,
        json=_error("request", "no_encontrado", "No encontrado."),
    )
    with pytest.raises(NotFoundError):
        client.crear_embed_token("cliente-1", "no-existe")


def test_crear_conexion_afip_embed_token(client, httpx_mock):
    httpx_mock.add_response(
        method="POST",
        url=f"{_API}/clientes/cliente-1/conexion-afip/embed-token",
        json={
            "embed_url": "https://arca.test/embed/conexion-afip/xyz",
            "expires_at": "2026-08-23T22:30:00.000000Z",
        },
    )
    result = client.crear_conexion_afip_embed_token("cliente-1")
    assert result.embed_url == "https://arca.test/embed/conexion-afip/xyz"
    assert result.expires_at == datetime(2026, 8, 23, 22, 30, tzinfo=timezone.utc)


def test_crear_conexion_afip_embed_token_external_ref_ajeno_levanta_not_found_error(
    client, httpx_mock
):
    httpx_mock.add_response(
        method="POST",
        url=f"{_API}/clientes/no-existe/conexion-afip/embed-token",
        status_code=404,
        json=_error("request", "no_encontrado", "No encontrado."),
    )
    with pytest.raises(NotFoundError):
        client.crear_conexion_afip_embed_token("no-existe")


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
                    "emision": _emision_json(idempotency_key="lote-1"),
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
                "emision": _emision_json(
                    idempotency_key="nc-1", comprobante=_comprobante_json(tipo="NOTA_CREDITO")
                ),
            }
        ],
    )
    nota = _comprobante(
        idempotency_key="nc-1",
        comprobante_asociado=ComprobanteAsociado(tipo=1, punto_venta=3, numero=100),
    )
    resultados = client.emitir_lote_notas_credito("cliente-1", [nota])
    assert resultados[0].emision.comprobante.tipo == "NOTA_CREDITO"


def test_emitir_lote_notas_debito_manda_la_clave_notas_debito(client, httpx_mock):
    httpx_mock.add_response(
        method="POST",
        url=f"{_API}/clientes/cliente-1/notas-debito/lote",
        json=[
            {
                "idempotency_key": "nd-1",
                "ok": True,
                "emision": _emision_json(
                    idempotency_key="nd-1", comprobante=_comprobante_json(tipo="NOTA_DEBITO")
                ),
            }
        ],
    )
    nota = _comprobante(
        idempotency_key="nd-1",
        comprobante_asociado=ComprobanteAsociado(tipo=1, punto_venta=3, numero=100),
    )
    resultados = client.emitir_lote_notas_debito("cliente-1", [nota])
    assert resultados[0].emision.comprobante.tipo == "NOTA_DEBITO"


def test_emitir_lote_comprobantes_mas_de_200_items_levanta_request_error(client, httpx_mock):
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
        client.emitir_lote_comprobantes("cliente-1", [_comprobante()])


# ---------------------------------------------------------------------------
# Webhooks
# ---------------------------------------------------------------------------


def test_reenviar_webhook(client, httpx_mock):
    httpx_mock.add_response(
        method="POST",
        url=f"{_API}/clientes/cliente-1/comprobantes/factura-1/webhook/reenviar",
        status_code=202,
        json=_emision_json(estado="issued", webhook_delivered=True),
    )
    result = client.reenviar_webhook("cliente-1", "factura-1")
    assert result.webhook_delivered is True


# ---------------------------------------------------------------------------
# Sobre de error: {"error": {"type", "code", "message", "param"?, "afip"?}} --
# ramificar SIEMPRE por `code`/`type`, nunca por status_code a secas (dos code
# bien distintos pueden compartir status). Ver MIGRACION.md, puntos 1 y 2.
# ---------------------------------------------------------------------------


def test_error_expone_type_code_message_param(client, httpx_mock):
    """El ejemplo exacto de MIGRACION.md, punto 1 -- confirma que los cuatro campos del
    sobre llegan intactos a la excepción, no solo el texto de `message`."""
    httpx_mock.add_response(
        method="POST",
        url=f"{_API}/clientes/cliente-1/comprobantes",
        status_code=422,
        json=_error("request", "campo_invalido", "items: can't be blank", param="items"),
    )
    with pytest.raises(RequestError) as exc_info:
        client.emitir_comprobante("cliente-1", _comprobante())
    assert exc_info.value.type == "request"
    assert exc_info.value.code == "campo_invalido"
    assert exc_info.value.message == "items: can't be blank"
    assert exc_info.value.param == "items"


def test_code_desconocido_cae_en_la_excepcion_de_su_type(client, httpx_mock):
    """`code` crece; `type` no (MIGRACION.md, punto 1) -- un `code` que este SDK todavía
    no conoce no debe romper ni perderse: tiene que caer en la excepción genérica de su
    `type`, con el `code` real igual accesible en `.code`."""
    httpx_mock.add_response(
        method="POST",
        url=f"{_API}/clientes/cliente-1/comprobantes",
        status_code=422,
        json=_error("configuracion", "un_code_que_todavia_no_existe", "Trámite pendiente."),
    )
    with pytest.raises(ConfiguracionError) as exc_info:
        client.emitir_comprobante("cliente-1", _comprobante())
    assert exc_info.value.code == "un_code_que_todavia_no_existe"
    assert not isinstance(exc_info.value, PuntoVentaNoHabilitadoError)


def test_punto_venta_no_habilitado_levanta_configuracion_error(client, httpx_mock):
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
        client.emitir_comprobante("cliente-1", _comprobante())
    assert isinstance(exc_info.value, ConfiguracionError)
    assert exc_info.value.status_code == 422


def test_nota_excede_comprobante_levanta_request_error_con_param(client, httpx_mock):
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
        client.emitir_nota_credito("cliente-1", nota)
    assert isinstance(exc_info.value, RequestError)
    assert exc_info.value.param == "comprobante_asociado"


def test_afip_rechazo_expone_los_codigos_de_afip_sin_masticar(client, httpx_mock):
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
        client.emitir_comprobante("cliente-1", _comprobante())
    assert isinstance(exc_info.value, AfipError)
    assert exc_info.value.afip == (
        AfipErrorDetail(codigo=10016, mensaje="La fecha del comprobante está fuera de rango"),
    )


def test_409_levanta_idempotency_conflict_error(client, httpx_mock):
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
        client.emitir_comprobante("cliente-1", _comprobante())


def test_404_levanta_not_found_error(client, httpx_mock):
    httpx_mock.add_response(
        method="GET",
        url=f"{_API}/clientes/cliente-1/comprobantes/no-existe",
        status_code=404,
        json=_error("request", "no_encontrado", "Not Found"),
    )
    with pytest.raises(NotFoundError) as exc_info:
        client.get_comprobante("cliente-1", "no-existe")
    assert exc_info.value.status_code == 404
    assert exc_info.value.message == "Not Found"


def test_429_levanta_rate_limited_error_con_retry_after(client, httpx_mock):
    httpx_mock.add_response(
        method="POST",
        url=f"{_API}/clientes/cliente-1/comprobantes",
        status_code=429,
        headers={"Retry-After": "7"},
        json=_error("request", "rate_limit", "Demasiados requests — respetá Retry-After."),
    )
    with pytest.raises(RateLimitedError) as exc_info:
        client.emitir_comprobante("cliente-1", _comprobante())
    assert exc_info.value.retry_after == 7


def test_429_sin_retry_after_header_deja_retry_after_none(client, httpx_mock):
    httpx_mock.add_response(
        method="POST",
        url=f"{_API}/clientes/cliente-1/comprobantes",
        status_code=429,
        json=_error("request", "rate_limit", "..."),
    )
    with pytest.raises(RateLimitedError) as exc_info:
        client.emitir_comprobante("cliente-1", _comprobante())
    assert exc_info.value.retry_after is None


def test_502_levanta_afip_unavailable_error(client, httpx_mock):
    httpx_mock.add_response(
        method="GET",
        url=f"{_API}/clientes/cliente-1/credencial/puntos-venta",
        status_code=502,
        json=_error("afip", "afip_sin_respuesta", "AFIP no respondió: timeout"),
    )
    with pytest.raises(AfipUnavailableError) as exc_info:
        client.listar_puntos_de_venta("cliente-1")
    assert isinstance(exc_info.value, AfipError)


def test_503_levanta_servicio_no_disponible_error(client, httpx_mock):
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
        client.importar_credencial("cliente-1", "20301234563", "cert", "key")


def test_500_levanta_interno_error(client, httpx_mock):
    httpx_mock.add_response(
        method="GET",
        url=f"{_API}/clientes/cliente-1/comprobantes/factura-1",
        status_code=500,
        json=_error("interno", "error_interno", "Error interno del servicio."),
    )
    with pytest.raises(InternoError):
        client.get_comprobante("cliente-1", "factura-1")


def test_error_sin_sobre_json_no_rompe_el_parseo(client, httpx_mock):
    """Si el error NO viene como `{"error": {...}}` (ej. un proxy intermedio devolviendo
    HTML de error, o faltara el header `Accept`), `_parse_error_envelope` no debe
    levantar un error DISTINTO que oculte el original -- cae a `InternoError` con el
    texto crudo en `.message`."""
    httpx_mock.add_response(
        method="GET",
        url=f"{_API}/clientes/cliente-1/comprobantes/factura-1",
        status_code=502,
        html="<html>Bad Gateway</html>",
    )
    with pytest.raises(InternoError) as exc_info:
        client.get_comprobante("cliente-1", "factura-1")
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
