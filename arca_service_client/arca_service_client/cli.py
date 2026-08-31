"""arca_service_client.cli -- `arca-service-client request-invite`/`login`/`import`/
`whoami`, mismo patrón que `stripe login`/`gh auth login`/`aws configure`: un comando,
credenciales guardadas solas -- de ahí en más tu código de app nunca vuelve a tocar un
PEM a mano (ver `local_config.py` para el porqué y el formato del perfil guardado).
Entry point declarado en `pyproject.toml` (`[project.scripts]`).

`request-invite` es el paso ANTERIOR a `login`, para quien todavía no tiene un invite
code: pega contra `POST /api/v1/signup-requests` (público, sin auth, sin nada
criptográfico) y queda ahí -- un operador de arca-service la revisa a mano y entrega el
invite real por otro canal; no hay ninguna respuesta automática.

Deliberadamente sobre `httpx` crudo acá, no sobre `ArcaServiceClient` -- ese
constructor ya asume mTLS/api_key en mano, que es justo lo que `login` todavía no
tiene (el signup en sí solo pide el invite code, sin mTLS).

`login` genera el par RSA + CSR ACÁ, del lado cliente, y manda solo el CSR (`csr_pem`,
información pública) en el `POST /signup` -- arca-service lo valida y lo firma, nunca
ve la clave privada, ni un instante. Más correcto técnicamente que la alternativa (que
el server genere el par y te lo mande una vez) -- y que `arca-service` ya soporta las
dos, así que no hay ningún motivo para que el camino "profesional" (este CLI) use la
menos buena.

`import` es DISTINTO a los dos de arriba: a diferencia de `login` (que genera SU
PROPIO par mTLS acá), acá la clave privada del CERTIFICADO AFIP de un Cliente YA
EXISTE de antes (otro sistema, otra Plataforma, migración) -- no hay nada que generar,
solo transportarla con cuidado hasta arca-service. Por eso `--cert`/`--key` son RUTAS
de archivo (no el contenido como argumento): un valor así de sensible no debería
aparecer nunca en el historial de la shell ni en `ps aux`. Mismo criterio para
`--key-password` -- si preferís no pasarla como argumento por el mismo motivo,
`--key-password-prompt` la pide interactiva (oculta, vía `getpass`). Ya sobre
`ArcaServiceClient` (a diferencia de `request-invite`/`login`): esto asume que ya
corriste `login` antes -- hace falta mTLS/api_key para poder decirle a arca-service
"este Cliente es tuyo". El sellado de la clave (RSA-OAEP + AES-256-GCM, nunca en
claro en el body) lo hace `ArcaServiceClient.importar_credencial` -- ver
`crypto.py::seal`, y el porqué en su propio moduledoc."""

from __future__ import annotations

import argparse
import getpass
import os
import ssl
import sys

import httpx
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from . import __version__
from .client import ArcaServiceClient
from .crypto import EnvelopeError
from .exceptions import ArcaServiceError
from .local_config import (
    DEFAULT_PROFILE,
    CredentialsNotFoundError,
    Profile,
    config_dir,
    discard_pending_signup,
    load_pending_signup,
    load_profile,
    save_pending_signup,
    save_profile,
)


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "request-invite":
        return _request_invite(args)
    if args.command == "completar-signup":
        return _completar_signup(args)
    if args.command == "login":
        return _login(args)
    if args.command == "import":
        return _import_credencial(args)
    if args.command == "whoami":
        return _whoami(args)

    parser.print_help()
    return 1


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="arca-service-client")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command")

    request_invite = sub.add_parser(
        "request-invite",
        help="Sin invite todavía -- pedí acceso, un operador lo revisa y te contacta",
    )
    request_invite.add_argument(
        "--base-url", help="Ej. https://arca.mancino.dev (o env ARCA_SERVICE_BASE_URL)"
    )
    request_invite.add_argument("--name", help='Nombre de tu Plataforma (ej. "Mi Plataforma")')
    request_invite.add_argument("--slug", help='Identificador estable (ej. "mi-plataforma")')
    request_invite.add_argument("--contact-email")
    request_invite.add_argument(
        "--message", default="", help="Contexto opcional para quien revisa (para qué lo vas a usar)"
    )
    request_invite.add_argument(
        "--con-csr",
        action="store_true",
        help="Generar CSR+clave ahora y mandarlo con la solicitud -- si se aprueba, "
        "arca-service te aprovisiona la Plataforma completa (API key + certificado) en "
        "vez de solo un invite code. Completá el perfil después con `completar-signup`",
    )
    request_invite.add_argument(
        "--profile",
        default=DEFAULT_PROFILE,
        help="Sólo con --con-csr: dónde guardar la clave hasta que llegue el certificado",
    )

    completar_signup = sub.add_parser(
        "completar-signup",
        help="Ya te llegó el certificado de un `request-invite --con-csr` -- armá el perfil",
    )
    completar_signup.add_argument(
        "--cert", required=True, help="Ruta al certificado que te entregó arca-service"
    )
    completar_signup.add_argument(
        "--api-key", required=True, help="API key que te entregó arca-service"
    )
    completar_signup.add_argument("--profile", default=DEFAULT_PROFILE)

    login = sub.add_parser(
        "login",
        help="Signup self-serve -- crea tu Plataforma y guarda sus credenciales",
    )
    login.add_argument(
        "--base-url", help="Ej. https://arca.mancino.dev (o env ARCA_SERVICE_BASE_URL)"
    )
    login.add_argument("--invite", required=True, help="Invite code, entregado por un canal seguro")
    login.add_argument("--name", help='Nombre de tu Plataforma (ej. "Mi Plataforma")')
    login.add_argument("--slug", help='Identificador estable (ej. "mi-plataforma")')
    login.add_argument("--contact-email")
    login.add_argument("--profile", default=DEFAULT_PROFILE)
    login.add_argument(
        "--yes",
        action="store_true",
        help="Acepta los términos de uso sin preguntar (para uso no interactivo/CI)",
    )

    import_ = sub.add_parser(
        "import",
        help="Ya tenés certificado+clave AFIP de un Cliente (de otro lado) -- importalos",
    )
    import_.add_argument("--cuit", required=True, help="CUIT del Cliente dueño del certificado")
    import_.add_argument("--cert", required=True, help="Ruta al certificado AFIP, ej. cliente.crt")
    import_.add_argument(
        "--key", required=True, help="Ruta a la clave privada AFIP correspondiente, ej. cliente.key"
    )
    import_.add_argument(
        "--key-password",
        help="Passphrase de la clave privada, si está cifrada -- o usá --key-password-prompt "
        "para no pasarla como argumento",
    )
    import_.add_argument(
        "--key-password-prompt",
        action="store_true",
        help="Pedir la passphrase de forma interactiva y oculta, en vez de --key-password",
    )
    import_.add_argument(
        "--point-of-sale",
        type=int,
        default=0,
        help="Punto de venta ya habilitado para este certificado, si lo sabés (default: 0)",
    )
    import_.add_argument("--profile", default=DEFAULT_PROFILE)

    whoami = sub.add_parser("whoami", help="Identidad + vencimiento del perfil guardado")
    whoami.add_argument("--profile", default=DEFAULT_PROFILE)

    return parser


def _request_invite(args: argparse.Namespace) -> int:
    """`POST /api/v1/signup-requests` -- a diferencia de `_login`, sin invite, sin
    Authorization. `.get()` en vez de `data["..."]` al leer la respuesta -- no hay
    ningún secreto acá que perder por degradar en vez de reventar si el body viniera
    incompleto (a diferencia de `_login`, ver su propio manejo).

    `--con-csr` genera el par CSR+clave ACÁ (mismo mecanismo que `_login`) y manda el
    CSR con la solicitud -- si arca-service la aprueba, aprovisiona la Plataforma
    completa de una en vez de solo un invite code. La clave privada nunca viaja: queda
    guardada localmente (`save_pending_signup`) hasta que llegue el certificado real, y
    `completar-signup` la empareja con ese certificado para terminar el perfil."""
    base_url = args.base_url or os.environ.get("ARCA_SERVICE_BASE_URL")
    if not base_url:
        print(
            "Falta --base-url (o la variable de entorno ARCA_SERVICE_BASE_URL).",
            file=sys.stderr,
        )
        return 1

    name = args.name or input('Nombre de tu Plataforma (ej. "Mi Plataforma"): ').strip()
    slug = args.slug or input('Slug, identificador estable (ej. "mi-plataforma"): ').strip()
    contact_email = args.contact_email or input("Email de contacto: ").strip()

    payload = {
        "name": name,
        "slug": slug,
        "contact_email": contact_email,
        "message": args.message,
    }
    key_pem = None
    if args.con_csr:
        csr_pem, key_pem = _generar_csr_y_clave(f"{slug}.arca-service")
        payload["csr_pem"] = csr_pem

    try:
        resp = httpx.post(
            f"{base_url.rstrip('/')}/api/v1/signup-requests",
            headers={"Accept": "application/json"},
            json=payload,
            timeout=30.0,
        )
    except httpx.HTTPError as exc:
        print(f"No se pudo contactar a {base_url}: {exc}", file=sys.stderr)
        return 1

    if resp.status_code != 201:
        print(f"El pedido falló ({resp.status_code}): {_mensaje_error(resp)}", file=sys.stderr)
        return 1

    try:
        data = resp.json()
    except ValueError:
        data = {}

    print(f"Solicitud recibida (id={data.get('id', '?')}).")
    print(
        data.get(
            "mensaje",
            "Un operador la va a revisar -- avisá si no tenés noticias en unos días.",
        )
    )

    if key_pem is not None:
        save_pending_signup(args.profile, base_url=base_url, slug=slug, key_pem=key_pem)
        print(
            f"Tu clave privada quedó guardada localmente. Cuando arca-service te "
            f"entregue el certificado, corré:\n"
            f"  arca-service-client completar-signup --cert <archivo.crt> "
            f"--api-key <api-key> --profile {args.profile!r}"
        )
    return 0


def _login(args: argparse.Namespace) -> int:
    base_url = args.base_url or os.environ.get("ARCA_SERVICE_BASE_URL")
    if not base_url:
        print(
            "Falta --base-url (o la variable de entorno ARCA_SERVICE_BASE_URL).",
            file=sys.stderr,
        )
        return 1

    name = args.name or input('Nombre de tu Plataforma (ej. "Mi Plataforma"): ').strip()
    slug = args.slug or input('Slug, identificador estable (ej. "mi-plataforma"): ').strip()
    contact_email = args.contact_email or input("Email de contacto: ").strip()

    if not args.yes and not _confirm("¿Aceptás los términos de uso de arca-service? [y/N] "):
        print("Signup cancelado -- hace falta aceptar los términos de uso.", file=sys.stderr)
        return 1

    # Generado ACÁ, antes de mandar nada -- ver el moduledoc de este archivo.
    csr_pem, key_pem = _generar_csr_y_clave(f"{slug}.arca-service")

    try:
        resp = httpx.post(
            f"{base_url.rstrip('/')}/api/v1/signup",
            headers={"Accept": "application/json", "Authorization": f"Bearer {args.invite}"},
            json={
                "name": name,
                "slug": slug,
                "contact_email": contact_email,
                "tos_ack": True,
                "csr_pem": csr_pem,
            },
            timeout=30.0,
        )
    except httpx.HTTPError as exc:
        print(f"No se pudo contactar a {base_url}: {exc}", file=sys.stderr)
        return 1

    if resp.status_code != 201:
        print(f"El signup falló ({resp.status_code}): {_mensaje_error(resp)}", file=sys.stderr)
        return 1

    try:
        data = resp.json()
        cert_pem = data["mtls_certificate_pem"]
        # data["mtls_private_key_pem"] es null -- mandamos csr_pem, arca-service nunca
        # tuvo esa clave. La nuestra es key_pem, de acá arriba; nunca viajó a ningún lado.

        profile = Profile(
            base_url=base_url,
            api_key=data["api_key"],
            # save_profile completa las rutas reales al escribir los archivos.
            client_cert_path="",
            client_key_path="",
            plataforma_slug=data["slug"],
            cert_not_after=_cert_not_after(cert_pem),
        )
        save_profile(args.profile, profile, cert_pem=cert_pem, key_pem=key_pem)
    except (ValueError, KeyError, TypeError) as exc:
        # 201 no garantiza que el body tenga la forma que esperamos -- esto
        # sería un bug del lado de arca-service, no algo que el usuario hizo
        # mal (a diferencia del camino de status != 201 de arriba, que sí es
        # esperable y ya tiene su propio manejo). ValueError cubre tanto JSON
        # inválido (resp.json()) como un PEM de certificado malformado
        # (_cert_not_after); KeyError/TypeError, un campo faltante o un body
        # que ni siquiera es un dict.
        print(
            f"El signup respondió 201 pero con datos inesperados ({exc!r}) -- "
            f"esto sería un bug de arca-service, no tuyo. Respuesta cruda: {resp.text}",
            file=sys.stderr,
        )
        return 1

    print(f"Listo -- Plataforma {data['slug']!r} guardada como perfil {args.profile!r}.")
    print(f"Configuración en: {config_dir()}")
    print("ArcaServiceClient()/AsyncArcaServiceClient() ya la usan sin argumentos.")
    return 0


def _completar_signup(args: argparse.Namespace) -> int:
    """Cierra el flujo de `request-invite --con-csr`: la clave privada ya está en disco
    desde ese llamado (nunca viajó a ningún lado, ver `save_pending_signup`), esto solo
    empareja el certificado real que te entregó arca-service por canal seguro y arma el
    perfil final -- mismo shape que deja `_login`, sin generar ningún CSR nuevo."""
    pending = load_pending_signup(args.profile)
    if pending is None:
        print(
            f"No hay ninguna solicitud con --con-csr pendiente para el perfil "
            f"{args.profile!r} -- corré `arca-service-client request-invite --con-csr` "
            "primero.",
            file=sys.stderr,
        )
        return 1

    cert_pem = _leer_archivo(args.cert, "el certificado")
    if cert_pem is None:
        return 1
    key_pem = _leer_archivo(pending.key_path, "la clave privada guardada")
    if key_pem is None:
        return 1

    # Confirma que el certificado es de verdad el par de ESTA clave antes de guardar
    # nada -- mismo motivo que el chequeo equivalente en `_import_credencial`: un
    # cert/clave que no corresponden revientan recién al abrir el `ssl_context` de
    # `ArcaServiceClient`, mucho más lejos de este error y sin este mensaje.
    try:
        ssl.create_default_context().load_cert_chain(certfile=args.cert, keyfile=pending.key_path)
    except ssl.SSLError as exc:
        print(f"El certificado no corresponde a la clave guardada: {exc}", file=sys.stderr)
        return 1

    profile = Profile(
        base_url=pending.base_url,
        api_key=args.api_key,
        client_cert_path="",
        client_key_path="",
        plataforma_slug=pending.slug,
        cert_not_after=_cert_not_after(cert_pem),
    )
    save_profile(args.profile, profile, cert_pem=cert_pem, key_pem=key_pem)
    discard_pending_signup(args.profile)

    print(f"Listo -- Plataforma {pending.slug!r} guardada como perfil {args.profile!r}.")
    print(f"Configuración en: {config_dir()}")
    print("ArcaServiceClient()/AsyncArcaServiceClient() ya la usan sin argumentos.")
    return 0


def _generar_csr_y_clave(cn: str) -> tuple[str, str]:
    """Par RSA 2048 + CSR generados LOCALMENTE -- devuelve `(csr_pem, key_pem)`.
    `key_pem` nunca sale de esta función salvo hacia `save_profile` (disco local,
    `chmod 0600`) -- no se manda en el `POST /signup`, solo `csr_pem` (información
    pública: quién sos, no cómo demostrarlo)."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    csr = (
        x509.CertificateSigningRequestBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)]))
        .sign(key, hashes.SHA256())
    )
    csr_pem = csr.public_bytes(serialization.Encoding.PEM).decode()
    key_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    return csr_pem, key_pem


def _import_credencial(args: argparse.Namespace) -> int:
    """`ArcaServiceClient.importar_credencial` vía CLI -- para un Cliente cuyo
    certificado+clave AFIP ya existen de antes. `cert_pem`/`key_pem` se leen de archivo
    acá (nunca como texto en un argumento, ver moduledoc) y se pasan tal cual: el
    sellado (`crypto.py::seal`) lo hace el propio método del cliente, esta función no
    toca esa parte."""
    cert_pem = _leer_archivo(args.cert, "el certificado")
    key_pem = _leer_archivo(args.key, "la clave privada")
    if cert_pem is None or key_pem is None:
        return 1

    key_password = args.key_password
    if key_password is None and args.key_password_prompt:
        key_password = (
            getpass.getpass("Passphrase de la clave privada (Enter si no tiene): ") or None
        )

    try:
        with ArcaServiceClient(profile=args.profile) as client:
            external_ref = client.por_cuit(args.cuit).external_ref
            resultado = client.importar_credencial(
                external_ref,
                args.cuit,
                cert_pem,
                key_pem,
                key_password=key_password,
                point_of_sale=args.point_of_sale,
            )
    except CredentialsNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except EnvelopeError as exc:
        print(f"No se pudo sellar la clave privada: {exc}", file=sys.stderr)
        return 1
    except ArcaServiceError as exc:
        print(
            f"arca-service rechazó la importación ({exc.status_code} {exc.code}): {exc.message}",
            file=sys.stderr,
        )
        return 1
    except httpx.HTTPError as exc:
        print(f"No se pudo contactar a arca-service: {exc}", file=sys.stderr)
        return 1

    print(
        f"Credencial importada para CUIT {args.cuit} -- "
        f"punto de venta: {resultado.point_of_sale}, activa: {resultado.active}."
    )
    return 0


def _leer_archivo(path: str, descripcion: str) -> str | None:
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except OSError as exc:
        print(f"No se pudo leer {descripcion} ({path}): {exc}", file=sys.stderr)
        return None


def _whoami(args: argparse.Namespace) -> int:
    try:
        profile = load_profile(args.profile)
    except CredentialsNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(f"Perfil:      {args.profile}")
    print(f"Plataforma:  {profile.plataforma_slug or '(desconocido)'}")
    print(f"base_url:    {profile.base_url}")
    print(f"Certificado: {profile.client_cert_path}")
    if profile.cert_not_after:
        print(f"Vence:       {profile.cert_not_after}")
    return 0


def _confirm(prompt: str) -> bool:
    return input(prompt).strip().lower() in ("y", "yes", "s", "si", "sí")


def _cert_not_after(cert_pem: str) -> str:
    # `not_valid_after_utc` (cryptography >= 42, devuelve un datetime YA aware) si
    # está -- si no, `.not_valid_after` (deprecada desde 42 pero la única disponible
    # en versiones más viejas; este paquete no fija un piso de versión). Esta última
    # devuelve naive -- X.509 codifica la vigencia siempre en UTC, de ahí el "Z" a
    # mano; la primera ya viene con offset, agregarlo de nuevo la rompería.
    cert = x509.load_pem_x509_certificate(cert_pem.encode())
    if hasattr(cert, "not_valid_after_utc"):
        return cert.not_valid_after_utc.isoformat()
    return cert.not_valid_after.isoformat() + "Z"


def _mensaje_error(resp: httpx.Response) -> str:
    """`error.message` del sobre `{"error": {"type", "code", "message", ...}}` -- mismo
    sobre que `ArcaServiceClient`/`AsyncArcaServiceClient` (ver `exceptions.py`), pero
    reimplementado acá en vez de importado: `request-invite`/`login` corren ANTES de
    tener ningún `ArcaServiceClient` (ni mTLS ni API key todavía), sobre `httpx` crudo,
    y no vale la pena acoplar este módulo al parseo interno de `client.py` por una
    función tan chica. Si el body no tiene esa forma (proxy intermedio, HTML de error),
    no rompe acá -- cae al texto crudo en vez de un `KeyError`/`JSONDecodeError` que
    ocultaría el error real detrás de OTRO error."""
    try:
        data = resp.json()
        if isinstance(data, dict) and isinstance(data.get("error"), dict):
            return str(data["error"].get("message", data["error"]))
    except ValueError:
        pass
    return resp.text


if __name__ == "__main__":
    sys.exit(main())
