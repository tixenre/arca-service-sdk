"""arca_service_client.cli -- `arca-service-client login`/`whoami`, mismo patrón que
`stripe login`/`gh auth login`/`aws configure`: un comando, credenciales guardadas
solas -- de ahí en más tu código de app nunca vuelve a tocar un PEM a mano (ver
`local_config.py` para el porqué y el formato del perfil guardado). Entry point
declarado en `pyproject.toml` (`[project.scripts]`).

Deliberadamente sobre `httpx` crudo acá, no sobre `ArcaServiceClient` -- ese
constructor ya asume mTLS/api_key en mano, que es justo lo que `login` todavía no
tiene (el signup en sí solo pide el invite code, sin mTLS -- ver
`ArcaServicePhxWeb.Plugs.SignupInviteAuth` del lado de arca-service).

`login` genera el par RSA + CSR ACÁ, del lado cliente, y manda solo el CSR (`csr_pem`,
información pública) en el `POST /signup` -- arca-service lo valida y lo firma, nunca
ve la clave privada, ni un instante (ver SECURITY.md/`ArcaServicePhx.Mtls` moduledoc de
arca-service). Más correcto técnicamente que la alternativa (que el server genere el
par y te lo mande una vez) -- y que `arca-service` ya soporta las dos, así que no hay
ningún motivo para que el camino "profesional" (este CLI) use la menos buena."""

from __future__ import annotations

import argparse
import os
import sys

import httpx
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from . import __version__
from .local_config import (
    DEFAULT_PROFILE,
    CredentialsNotFoundError,
    Profile,
    config_dir,
    load_profile,
    save_profile,
)


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "login":
        return _login(args)
    if args.command == "whoami":
        return _whoami(args)

    parser.print_help()
    return 1


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="arca-service-client")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command")

    login = sub.add_parser(
        "login",
        help="Signup self-serve -- crea tu Plataforma y guarda sus credenciales",
    )
    login.add_argument(
        "--base-url", help="Ej. https://arca.tudominio.com (o env ARCA_SERVICE_BASE_URL)"
    )
    login.add_argument("--invite", required=True, help="Invite code, entregado por un canal seguro")
    login.add_argument("--name", help='Nombre de tu Plataforma (ej. "Rambla")')
    login.add_argument("--slug", help='Identificador estable (ej. "rambla")')
    login.add_argument("--contact-email")
    login.add_argument("--profile", default=DEFAULT_PROFILE)
    login.add_argument(
        "--yes",
        action="store_true",
        help="Acepta los términos de uso sin preguntar (para uso no interactivo/CI)",
    )

    whoami = sub.add_parser("whoami", help="Identidad + vencimiento del perfil guardado")
    whoami.add_argument("--profile", default=DEFAULT_PROFILE)

    return parser


def _login(args: argparse.Namespace) -> int:
    base_url = args.base_url or os.environ.get("ARCA_SERVICE_BASE_URL")
    if not base_url:
        print(
            "Falta --base-url (o la variable de entorno ARCA_SERVICE_BASE_URL).",
            file=sys.stderr,
        )
        return 1

    name = args.name or input('Nombre de tu Plataforma (ej. "Rambla"): ').strip()
    slug = args.slug or input('Slug, identificador estable (ej. "rambla"): ').strip()
    contact_email = args.contact_email or input("Email de contacto: ").strip()

    if not args.yes and not _confirm("¿Aceptás los términos de uso de arca-service? [y/N] "):
        print("Signup cancelado -- hace falta aceptar los términos de uso.", file=sys.stderr)
        return 1

    # Generado ACÁ, antes de mandar nada -- ver el moduledoc de este archivo.
    csr_pem, key_pem = _generar_csr_y_clave(f"{slug}.arca-service")

    try:
        resp = httpx.post(
            f"{base_url.rstrip('/')}/api/v1/signup",
            headers={"Authorization": f"Bearer {args.invite}"},
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
        print(f"El signup falló ({resp.status_code}): {_detail(resp)}", file=sys.stderr)
        return 1

    try:
        data = resp.json()
        cert_pem = data["mtls_certificate_pem"]
        # data["mtls_private_key_pem"] es null -- mandamos csr_pem, arca-service nunca
        # tuvo esa clave (ver SECURITY.md de arca-service). La nuestra es key_pem, de acá
        # arriba; nunca viajó a ningún lado.

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


def _detail(resp: httpx.Response) -> str:
    try:
        data = resp.json()
        if isinstance(data, dict) and "detail" in data:
            return str(data["detail"])
    except ValueError:
        pass
    return resp.text


if __name__ == "__main__":
    sys.exit(main())
