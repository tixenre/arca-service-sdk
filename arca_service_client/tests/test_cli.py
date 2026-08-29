"""Tests de cli.py -- `login`/`import`/`whoami`. `httpx_mock` (pytest-httpx) mockea el
`POST /signup`; `isolated_config_dir` (conftest.py) aísla el perfil guardado en un
`tmp_path` propio de cada test."""

from __future__ import annotations

import json

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from arca_service_client import cli
from arca_service_client.local_config import (
    DEFAULT_PROFILE,
    CredentialsNotFoundError,
    Profile,
    load_profile,
    save_profile,
)

_BASE_URL = "https://arca.test"
_API = f"{_BASE_URL}/api/v1"
_SIGNUP_URL = f"{_BASE_URL}/api/v1/signup"
_SIGNUP_REQUESTS_URL = f"{_BASE_URL}/api/v1/signup-requests"


def _clave_publica_de_test() -> str:
    """Clave pública RSA real (no un string cualquiera) -- `crypto.seal()` la parsea de
    verdad y cifra contra ella, así que `--cert`/`--key` de un test de `import` llegan
    hasta ahí. Mismo helper que `test_client.py::_clave_publica_de_test`, repetido acá
    a propósito -- cada archivo de test de este paquete se mantiene autocontenido."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return (
        key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode()
    )


def _login_args(**overrides):
    args = {
        "--base-url": _BASE_URL,
        "--invite": "invite_abc",
        "--name": "Acme",
        "--slug": "acme",
        "--contact-email": "dev@acme.example",
    }
    args.update(overrides)
    argv = ["login", "--yes"]
    for flag, value in args.items():
        argv.extend([flag, value])
    return argv


def _mock_signup_ok(httpx_mock, cert_pem, *, slug="acme"):
    httpx_mock.add_response(
        method="POST",
        url=_SIGNUP_URL,
        status_code=201,
        json={
            "plataforma_id": "abc-123",
            "slug": slug,
            "api_key": "arca_test-key",
            "mtls_certificate_pem": cert_pem,
            "mtls_private_key_pem": None,
            "mensaje": "...",
        },
    )


def _request_invite_args(**overrides):
    args = {
        "--base-url": _BASE_URL,
        "--name": "Acme",
        "--slug": "acme",
        "--contact-email": "dev@acme.example",
    }
    args.update(overrides)
    argv = ["request-invite"]
    for flag, value in args.items():
        argv.extend([flag, value])
    return argv


def test_request_invite_feliz_imprime_lo_que_devuelve_el_server(httpx_mock, capsys):
    httpx_mock.add_response(
        method="POST",
        url=_SIGNUP_REQUESTS_URL,
        status_code=201,
        json={"id": "req-123", "name": "Acme", "slug": "acme", "mensaje": "Recibido."},
    )

    assert cli.main(_request_invite_args()) == 0

    salida = capsys.readouterr().out
    assert "req-123" in salida
    assert "Recibido." in salida


def test_request_invite_no_manda_ningun_authorization(httpx_mock):
    # A diferencia de login (Bearer <invite>) -- pedir el invite no puede exigir ya
    # tenerlo.
    httpx_mock.add_response(
        method="POST",
        url=_SIGNUP_REQUESTS_URL,
        status_code=201,
        json={"id": "req-123"},
    )

    cli.main(_request_invite_args())

    [request] = httpx_mock.get_requests()
    assert "authorization" not in {h.lower() for h in request.headers.keys()}


def test_request_invite_manda_accept_application_json(httpx_mock):
    # Sin este header, un error del servidor vuelve como texto plano en vez del sobre
    # `{"error": {...}}` -- ver `_mensaje_error` en cli.py y el mismo criterio en
    # client.py. `request-invite`/`login` corren sobre httpx crudo, así que este header
    # no viene gratis de ningún cliente compartido.
    httpx_mock.add_response(
        method="POST",
        url=_SIGNUP_REQUESTS_URL,
        status_code=201,
        json={"id": "req-123"},
    )

    cli.main(_request_invite_args())

    [request] = httpx_mock.get_requests()
    assert request.headers["accept"] == "application/json"


def test_request_invite_rechazado_por_el_server_falla_claro(httpx_mock, capsys):
    httpx_mock.add_response(
        method="POST",
        url=_SIGNUP_REQUESTS_URL,
        status_code=422,
        json={
            "error": {
                "type": "request",
                "code": "campo_invalido",
                "message": "contact_email: no es un email válido",
                "param": "contact_email",
            }
        },
    )

    exit_code = cli.main(_request_invite_args(**{"--contact-email": "no-es-un-email"}))

    assert exit_code == 1
    assert "no es un email válido" in capsys.readouterr().err


def test_request_invite_sin_base_url_ni_env_var_falla_claro(monkeypatch, capsys):
    monkeypatch.delenv("ARCA_SERVICE_BASE_URL", raising=False)

    argv = ["request-invite", "--name", "n", "--slug", "s", "--contact-email", "e@e.com"]

    assert cli.main(argv) == 1
    assert "base-url" in capsys.readouterr().err.lower()


def test_request_invite_prompts_interactivos_cuando_faltan_flags(monkeypatch, httpx_mock):
    httpx_mock.add_response(
        method="POST",
        url=_SIGNUP_REQUESTS_URL,
        status_code=201,
        json={"id": "req-456"},
    )

    respuestas = iter(["Beta", "beta", "dev@beta.example"])
    monkeypatch.setattr("builtins.input", lambda *_args: next(respuestas))

    exit_code = cli.main(["request-invite", "--base-url", _BASE_URL])

    assert exit_code == 0
    [request] = httpx_mock.get_requests()
    body = json.loads(request.content)
    assert body == {
        "name": "Beta",
        "slug": "beta",
        "contact_email": "dev@beta.example",
        "message": "",
    }


def test_request_invite_201_con_body_no_json_no_revienta(httpx_mock, capsys):
    # Mismo espíritu que el fix de _login para datos inesperados en un 201 -- acá no
    # hay ningún secreto que guardar, así que alcanza con degradar en vez de reventar.
    httpx_mock.add_response(
        method="POST",
        url=_SIGNUP_REQUESTS_URL,
        status_code=201,
        content=b"esto no es json",
    )

    exit_code = cli.main(_request_invite_args())

    assert exit_code == 0
    assert "id=?" in capsys.readouterr().out


def test_login_feliz_guarda_el_perfil(isolated_config_dir, client_cert_pem, httpx_mock):
    cert_pem, _ignorado = client_cert_pem
    _mock_signup_ok(httpx_mock, cert_pem)

    assert cli.main(_login_args()) == 0

    profile = load_profile("default")
    assert profile.api_key == "arca_test-key"
    assert profile.plataforma_slug == "acme"
    assert profile.base_url == _BASE_URL


def test_login_guarda_la_clave_generada_localmente_en_disco(
    isolated_config_dir, client_cert_pem, httpx_mock
):
    cert_pem, _ignorado = client_cert_pem
    _mock_signup_ok(httpx_mock, cert_pem)

    cli.main(_login_args())

    profile = load_profile("default")
    with open(profile.client_key_path) as f:
        clave_guardada = f.read()
    assert "PRIVATE KEY" in clave_guardada
    with open(profile.client_cert_path) as f:
        assert f.read() == cert_pem


def test_login_manda_un_csr_real_con_el_cn_correcto_y_nunca_una_clave_privada(
    isolated_config_dir, client_cert_pem, httpx_mock
):
    cert_pem, _ignorado = client_cert_pem
    _mock_signup_ok(httpx_mock, cert_pem)

    cli.main(_login_args())

    [request] = httpx_mock.get_requests()
    body = json.loads(request.content)

    assert "csr_pem" in body
    assert "BEGIN CERTIFICATE REQUEST" in body["csr_pem"]
    # El body entero, no solo un campo puntual -- ninguna clave privada tiene que
    # cruzar la red bajo ningún nombre de campo.
    assert "PRIVATE KEY" not in json.dumps(body)

    csr = x509.load_pem_x509_csr(body["csr_pem"].encode())
    [cn] = csr.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
    assert cn.value == "acme.arca-service"


def test_login_manda_accept_application_json(isolated_config_dir, client_cert_pem, httpx_mock):
    cert_pem, _ignorado = client_cert_pem
    _mock_signup_ok(httpx_mock, cert_pem)

    cli.main(_login_args())

    [request] = httpx_mock.get_requests()
    assert request.headers["accept"] == "application/json"


def test_login_sin_base_url_ni_env_var_falla_claro(monkeypatch, capsys):
    monkeypatch.delenv("ARCA_SERVICE_BASE_URL", raising=False)

    argv = [
        "login",
        "--invite",
        "x",
        "--yes",
        "--name",
        "n",
        "--slug",
        "s",
        "--contact-email",
        "e@e.com",
    ]

    assert cli.main(argv) == 1
    assert "base-url" in capsys.readouterr().err.lower()


def test_login_usa_arca_service_base_url_del_entorno_si_no_hay_flag(
    monkeypatch, isolated_config_dir, client_cert_pem, httpx_mock
):
    monkeypatch.setenv("ARCA_SERVICE_BASE_URL", _BASE_URL)
    cert_pem, _ignorado = client_cert_pem
    _mock_signup_ok(httpx_mock, cert_pem)

    argv = [
        "login",
        "--invite",
        "invite_abc",
        "--yes",
        "--name",
        "Acme",
        "--slug",
        "acme",
        "--contact-email",
        "dev@acme.example",
    ]

    assert cli.main(argv) == 0
    assert load_profile("default").base_url == _BASE_URL


def test_login_signup_rechazado_no_guarda_nada(isolated_config_dir, httpx_mock):
    httpx_mock.add_response(
        method="POST",
        url=_SIGNUP_URL,
        status_code=403,
        json={
            "error": {
                "type": "request",
                "code": "invitacion_invalida",
                "message": "Código de invitación inválido.",
            }
        },
    )

    exit_code = cli.main(_login_args(**{"--invite": "codigo-malo"}))

    assert exit_code == 1
    with pytest.raises(CredentialsNotFoundError):
        load_profile("default")


def test_login_201_con_campo_faltante_no_revienta_ni_guarda_nada(
    isolated_config_dir, httpx_mock, capsys
):
    # 201 pero sin api_key/mtls_certificate_pem -- bug del lado de
    # arca-service, no algo que el usuario hizo mal. Antes de este fix esto
    # reventaba con un KeyError crudo en vez de un error prolijo (ver cli.py).
    httpx_mock.add_response(
        method="POST",
        url=_SIGNUP_URL,
        status_code=201,
        json={"plataforma_id": "abc-123", "slug": "acme"},
    )

    exit_code = cli.main(_login_args())

    assert exit_code == 1
    assert "inesperados" in capsys.readouterr().err
    with pytest.raises(CredentialsNotFoundError):
        load_profile("default")


def test_login_201_con_body_no_json_no_revienta_ni_guarda_nada(
    isolated_config_dir, httpx_mock, capsys
):
    httpx_mock.add_response(
        method="POST",
        url=_SIGNUP_URL,
        status_code=201,
        content=b"esto no es json",
    )

    exit_code = cli.main(_login_args())

    assert exit_code == 1
    assert "inesperados" in capsys.readouterr().err
    with pytest.raises(CredentialsNotFoundError):
        load_profile("default")


def test_login_sin_aceptar_tos_no_llama_a_la_red(monkeypatch, isolated_config_dir, httpx_mock):
    monkeypatch.setattr("builtins.input", lambda *_args: "n")  # no acepta el prompt de ToS
    argv = [
        "login",
        "--base-url",
        _BASE_URL,
        "--invite",
        "x",
        "--name",
        "n",
        "--slug",
        "s",
        "--contact-email",
        "e@e.com",
    ]

    exit_code = cli.main(argv)  # sin --yes -- pasa por el prompt de confirmación

    assert exit_code == 1
    assert httpx_mock.get_requests() == []


def test_login_prompts_interactivos_cuando_faltan_flags(
    monkeypatch, isolated_config_dir, client_cert_pem, httpx_mock
):
    cert_pem, _ignorado = client_cert_pem
    _mock_signup_ok(httpx_mock, cert_pem, slug="beta")

    respuestas = iter(["Beta", "beta", "dev@beta.example", "y"])
    monkeypatch.setattr("builtins.input", lambda *_args: next(respuestas))

    exit_code = cli.main(["login", "--base-url", _BASE_URL, "--invite", "invite_abc"])

    assert exit_code == 0
    assert load_profile("default").plataforma_slug == "beta"


def test_whoami_sin_perfil_guardado_falla_claro(isolated_config_dir, capsys):
    exit_code = cli.main(["whoami"])

    assert exit_code == 1
    assert "login" in capsys.readouterr().err


def test_whoami_con_perfil_guardado_muestra_la_identidad(
    isolated_config_dir, client_cert_pem, httpx_mock, capsys
):
    cert_pem, _ignorado = client_cert_pem
    _mock_signup_ok(httpx_mock, cert_pem)
    cli.main(_login_args())
    capsys.readouterr()  # descarta la salida del login

    exit_code = cli.main(["whoami"])

    salida = capsys.readouterr().out
    assert exit_code == 0
    assert "acme" in salida
    assert _BASE_URL in salida


# ---------------------------------------------------------------------------
# import -- certificado+clave AFIP que un Cliente ya tenía de antes (de otro
# lado, o de otra Plataforma). A diferencia de login/request-invite, esto
# corre CON un perfil ya guardado -- cada test que lo necesita lo arma con
# `_con_perfil_guardado` (guarda uno directo, ver por qué no vía `login` en
# su propio comentario).
# ---------------------------------------------------------------------------


def _con_perfil_guardado(isolated_config_dir, client_cert_pem):
    # A diferencia de correr `login` de verdad (que generaría SU PROPIA clave,
    # distinta del cert self-signed que devuelve el mock -- mismatch inofensivo
    # para los tests de whoami/login de arriba, que nunca abren TLS de verdad
    # con el perfil guardado) -- import SÍ construye un `ArcaServiceClient`
    # real, que valida cert/clave al abrir el `ssl_context` (`load_cert_chain`
    # revienta con `KEY_VALUES_MISMATCH` si no coinciden). `client_cert_pem` es
    # un PAR ya consistente (mismo `_self_signed_cert_and_key_pem()`, ver
    # conftest.py) -- guardarlo tal cual evita ese problema sin tener que
    # pasar por el mock de `POST /signup`.
    cert_pem, key_pem = client_cert_pem
    save_profile(
        DEFAULT_PROFILE,
        Profile(
            base_url=_BASE_URL,
            api_key="arca_test-key",
            client_cert_path="",
            client_key_path="",
            plataforma_slug="acme",
        ),
        cert_pem=cert_pem,
        key_pem=key_pem,
    )


def _archivos_cert_y_clave(tmp_path):
    # Contenido placeholder -- ni cli.py ni el server (mockeado acá) lo
    # parsean de verdad, solo se lee y se manda tal cual (ver
    # test_client.py, mismo criterio para su propio "-----BEGIN
    # CERTIFICATE-----...").
    cert_path = tmp_path / "afip.crt"
    key_path = tmp_path / "afip.key"
    cert_path.write_text("-----BEGIN CERTIFICATE-----...")
    key_path.write_text("-----BEGIN PRIVATE KEY-----...")
    return str(cert_path), str(key_path)


def _import_args(cert_path, key_path, **overrides):
    args = {"--cuit": "20301234563", "--cert": cert_path, "--key": key_path}
    args.update(overrides)
    argv = ["import"]
    for flag, value in args.items():
        argv.extend([flag, value])
    return argv


def test_import_feliz_importa_la_credencial(
    isolated_config_dir, client_cert_pem, httpx_mock, capsys, tmp_path
):
    _con_perfil_guardado(isolated_config_dir, client_cert_pem)
    cert_path, key_path = _archivos_cert_y_clave(tmp_path)

    httpx_mock.add_response(
        method="POST", url=f"{_API}/clientes/por-cuit", json={"external_ref": "cliente-1"}
    )
    httpx_mock.add_response(
        method="GET",
        url=f"{_API}/envelope/clave-publica",
        json={"public_key_pem": _clave_publica_de_test()},
    )
    httpx_mock.add_response(
        method="POST",
        url=f"{_API}/clientes/cliente-1/credencial/importar",
        json={"point_of_sale": 5, "active": True},
    )

    exit_code = cli.main(_import_args(cert_path, key_path))

    salida = capsys.readouterr().out
    assert exit_code == 0
    assert "5" in salida
    assert "activa: True" in salida


def test_import_nunca_manda_la_clave_privada_en_claro(
    isolated_config_dir, client_cert_pem, httpx_mock, capsys, tmp_path
):
    """El punto entero de `importar_credencial` -- ver moduledoc de cli.py y de
    crypto.py. Si esto alguna vez fallara sería el bug más grave posible acá."""
    _con_perfil_guardado(isolated_config_dir, client_cert_pem)
    cert_path, key_path = _archivos_cert_y_clave(tmp_path)

    httpx_mock.add_response(
        method="POST", url=f"{_API}/clientes/por-cuit", json={"external_ref": "cliente-1"}
    )
    httpx_mock.add_response(
        method="GET",
        url=f"{_API}/envelope/clave-publica",
        json={"public_key_pem": _clave_publica_de_test()},
    )
    httpx_mock.add_response(
        method="POST",
        url=f"{_API}/clientes/cliente-1/credencial/importar",
        json={"point_of_sale": 0, "active": True},
    )

    cli.main(_import_args(cert_path, key_path))

    [_por_cuit, _clave_publica, importar] = httpx_mock.get_requests()
    body = json.loads(importar.content)
    assert "PRIVATE KEY" not in json.dumps(body)
    assert set(body["sealed"].keys()) == {"v", "ek", "n", "ct"}


def test_import_sin_perfil_guardado_falla_claro(isolated_config_dir, capsys, tmp_path):
    cert_path, key_path = _archivos_cert_y_clave(tmp_path)

    exit_code = cli.main(_import_args(cert_path, key_path))

    assert exit_code == 1
    assert "login" in capsys.readouterr().err


def test_import_certificado_inexistente_falla_claro_sin_pegarle_a_la_red(
    isolated_config_dir, client_cert_pem, httpx_mock, capsys, tmp_path
):
    _con_perfil_guardado(isolated_config_dir, client_cert_pem)
    _cert_path, key_path = _archivos_cert_y_clave(tmp_path)

    exit_code = cli.main(_import_args(str(tmp_path / "no-existe.crt"), key_path))

    assert exit_code == 1
    assert "no-existe.crt" in capsys.readouterr().err
    assert httpx_mock.get_requests() == []


def test_import_rechazado_por_arca_service_falla_claro(
    isolated_config_dir, client_cert_pem, httpx_mock, capsys, tmp_path
):
    _con_perfil_guardado(isolated_config_dir, client_cert_pem)
    cert_path, key_path = _archivos_cert_y_clave(tmp_path)

    httpx_mock.add_response(
        method="POST", url=f"{_API}/clientes/por-cuit", json={"external_ref": "cliente-1"}
    )
    httpx_mock.add_response(
        method="GET",
        url=f"{_API}/envelope/clave-publica",
        json={"public_key_pem": _clave_publica_de_test()},
    )
    httpx_mock.add_response(
        method="POST",
        url=f"{_API}/clientes/cliente-1/credencial/importar",
        status_code=422,
        json={
            "error": {
                "type": "configuracion",
                "code": "credencial_rechazada",
                "message": "El certificado no corresponde a la clave privada enviada.",
            }
        },
    )

    exit_code = cli.main(_import_args(cert_path, key_path))

    assert exit_code == 1
    assert "no corresponde a la clave privada" in capsys.readouterr().err


def test_import_key_password_prompt_no_lo_pide_si_ya_vino_por_flag(
    isolated_config_dir, client_cert_pem, httpx_mock, capsys, tmp_path, monkeypatch
):
    _con_perfil_guardado(isolated_config_dir, client_cert_pem)
    cert_path, key_path = _archivos_cert_y_clave(tmp_path)

    def _no_deberia_llamarse(*_args, **_kwargs):
        raise AssertionError("--key-password ya vino por flag, no debería pedirse interactivo")

    monkeypatch.setattr("getpass.getpass", _no_deberia_llamarse)

    httpx_mock.add_response(
        method="POST", url=f"{_API}/clientes/por-cuit", json={"external_ref": "cliente-1"}
    )
    httpx_mock.add_response(
        method="GET",
        url=f"{_API}/envelope/clave-publica",
        json={"public_key_pem": _clave_publica_de_test()},
    )
    httpx_mock.add_response(
        method="POST",
        url=f"{_API}/clientes/cliente-1/credencial/importar",
        json={"point_of_sale": 0, "active": True},
    )

    exit_code = cli.main(_import_args(cert_path, key_path, **{"--key-password": "hunter2"}))

    assert exit_code == 0


def test_import_key_password_prompt_pide_interactivo_si_no_vino_por_flag(
    isolated_config_dir, client_cert_pem, httpx_mock, capsys, tmp_path, monkeypatch
):
    _con_perfil_guardado(isolated_config_dir, client_cert_pem)
    cert_path, key_path = _archivos_cert_y_clave(tmp_path)

    monkeypatch.setattr("getpass.getpass", lambda *_args: "hunter2")

    httpx_mock.add_response(
        method="POST", url=f"{_API}/clientes/por-cuit", json={"external_ref": "cliente-1"}
    )
    httpx_mock.add_response(
        method="GET",
        url=f"{_API}/envelope/clave-publica",
        json={"public_key_pem": _clave_publica_de_test()},
    )
    httpx_mock.add_response(
        method="POST",
        url=f"{_API}/clientes/cliente-1/credencial/importar",
        json={"point_of_sale": 0, "active": True},
    )

    argv = _import_args(cert_path, key_path) + ["--key-password-prompt"]
    exit_code = cli.main(argv)

    assert exit_code == 0
