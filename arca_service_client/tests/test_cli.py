"""Tests de cli.py -- `login`/`whoami`. `httpx_mock` (pytest-httpx) mockea el
`POST /signup`; `isolated_config_dir` (conftest.py) aísla el perfil guardado en un
`tmp_path` propio de cada test."""

from __future__ import annotations

import json

import pytest
from cryptography import x509
from cryptography.x509.oid import NameOID

from arca_service_client import cli
from arca_service_client.local_config import CredentialsNotFoundError, load_profile

_BASE_URL = "https://arca.test"
_SIGNUP_URL = f"{_BASE_URL}/api/v1/signup"
_SIGNUP_REQUESTS_URL = f"{_BASE_URL}/api/v1/signup-requests"


def _login_args(**overrides):
    args = {
        "--base-url": _BASE_URL,
        "--invite": "invite_abc",
        "--name": "Rambla",
        "--slug": "rambla",
        "--contact-email": "dev@rambla.house",
    }
    args.update(overrides)
    argv = ["login", "--yes"]
    for flag, value in args.items():
        argv.extend([flag, value])
    return argv


def _mock_signup_ok(httpx_mock, cert_pem, *, slug="rambla"):
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
        "--name": "Rambla",
        "--slug": "rambla",
        "--contact-email": "dev@rambla.house",
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
        json={"id": "req-123", "name": "Rambla", "slug": "rambla", "mensaje": "Recibido."},
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


def test_request_invite_rechazado_por_el_server_falla_claro(httpx_mock, capsys):
    httpx_mock.add_response(
        method="POST",
        url=_SIGNUP_REQUESTS_URL,
        status_code=422,
        json={"detail": "contact_email: no es un email válido"},
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

    respuestas = iter(["Ganche", "ganche", "dev@ganche.com"])
    monkeypatch.setattr("builtins.input", lambda *_args: next(respuestas))

    exit_code = cli.main(["request-invite", "--base-url", _BASE_URL])

    assert exit_code == 0
    [request] = httpx_mock.get_requests()
    body = json.loads(request.content)
    assert body == {
        "name": "Ganche",
        "slug": "ganche",
        "contact_email": "dev@ganche.com",
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
    assert profile.plataforma_slug == "rambla"
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
    assert cn.value == "rambla.arca-service"


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
        "Rambla",
        "--slug",
        "rambla",
        "--contact-email",
        "dev@rambla.house",
    ]

    assert cli.main(argv) == 0
    assert load_profile("default").base_url == _BASE_URL


def test_login_signup_rechazado_no_guarda_nada(isolated_config_dir, httpx_mock):
    httpx_mock.add_response(
        method="POST",
        url=_SIGNUP_URL,
        status_code=403,
        json={"detail": "Código de invitación inválido."},
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
        json={"plataforma_id": "abc-123", "slug": "rambla"},
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
    _mock_signup_ok(httpx_mock, cert_pem, slug="ganche")

    respuestas = iter(["Ganche", "ganche", "dev@ganche.com", "y"])
    monkeypatch.setattr("builtins.input", lambda *_args: next(respuestas))

    exit_code = cli.main(["login", "--base-url", _BASE_URL, "--invite", "invite_abc"])

    assert exit_code == 0
    assert load_profile("default").plataforma_slug == "ganche"


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
    assert "rambla" in salida
    assert _BASE_URL in salida
