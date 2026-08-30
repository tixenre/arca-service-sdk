"""arca_service_client.local_config — perfiles guardados por `arca-service-client login`
(ver `cli.py`) para que `ArcaServiceClient()`/`AsyncArcaServiceClient()` se puedan
instanciar SIN argumentos en desarrollo local — mismo patrón que `~/.aws/credentials`
(AWS CLI) o `~/.config/gh/hosts.yml` (`gh` CLI): un comando de login una sola vez, el SDK
lee solo de ahí después.

Deliberadamente NO pensado para producción -- un container no tiene "tu" home
directory, y no debería depender de que alguien haya corrido `login` a mano ahí. En
producción seguí pasando `base_url`/`client_cert_path`/`client_key_path`/`api_key`
explícitos (env vars, tu secret manager). Este archivo resuelve la fricción de "tengo
que arrancar a desarrollar contra arca-service ahora mismo", no cambia nada del
contrato de producción.

Formato: un directorio (`~/.config/arca-service`, respetando `XDG_CONFIG_HOME` si está
seteado) con `credentials.json` (metadata + RUTAS a cert/key, nunca el PEM en sí) más un
`<profile>.crt`/`<profile>.key` por perfil -- separar el PEM del JSON es lo mismo que ya
espera `ArcaServiceClient(client_cert_path=..., client_key_path=...)`, así que cargar un
perfil es simplemente pasarle esas rutas tal cual. `chmod 0600` en cada archivo
sensible, `0700` en el directorio -- mismo criterio que `ssh-keygen`/`~/.ssh`. Best
effort: si `chmod` no está soportado tal cual (ej. Windows sin ACLs POSIX), no rompe el
login -- la restricción de permisos es defensa en profundidad, no la única barrera."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path

DEFAULT_PROFILE = "default"


class CredentialsNotFoundError(Exception):
    """No hay un perfil guardado con ese nombre -- corré `arca-service-client login`
    primero, o pasá `base_url`/`client_cert_path`/`client_key_path`/`api_key` explícitos
    (mismo criterio que producción, ver el moduledoc de este archivo). NO es un
    `ArcaServiceError` -- no es una respuesta HTTP de arca-service, es un problema de
    configuración local, antes de que exista ningún request."""


@dataclass
class Profile:
    """Un perfil guardado -- `plataforma_slug`/`cert_not_after` son de cortesía (los
    usa `whoami`, ver `cli.py`), nunca se mandan a arca-service ni se usan para
    resolver nada del lado servidor."""

    base_url: str
    api_key: str
    client_cert_path: str
    client_key_path: str
    plataforma_slug: str = ""
    cert_not_after: str | None = None


@dataclass
class PendingSignup:
    """Un `request-invite --con-csr` en danza: el CSR ya se mandó, la clave privada
    correspondiente ya está en disco (nunca viajó a ningún lado), pero el certificado
    todavía no llegó -- lo entrega un operador de arca-service por canal seguro después
    de aprobar la solicitud. `completar-signup` (ver cli.py) junta esta clave con ESE
    certificado para terminar de armar el perfil."""

    base_url: str
    slug: str
    key_path: str


def config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "arca-service"


def _credentials_path() -> Path:
    return config_dir() / "credentials.json"


def _pending_signups_path() -> Path:
    return config_dir() / "pending_signups.json"


def _chmod_best_effort(path: Path, mode: int) -> None:
    try:
        os.chmod(path, mode)
    except OSError:
        pass


def save_profile(name: str, profile: Profile, *, cert_pem: str, key_pem: str) -> None:
    """Escribe `<config_dir>/<name>.crt`/`.key` (el PEM en sí) y actualiza
    `credentials.json` con el resto (RUTAS, no el PEM) -- preserva cualquier otro
    perfil que ya estuviera guardado, nunca pisa el archivo entero."""
    directory = config_dir()
    directory.mkdir(parents=True, exist_ok=True)
    _chmod_best_effort(directory, 0o700)

    cert_path = directory / f"{name}.crt"
    key_path = directory / f"{name}.key"
    cert_path.write_text(cert_pem)
    key_path.write_text(key_pem)
    _chmod_best_effort(cert_path, 0o600)
    _chmod_best_effort(key_path, 0o600)

    profile.client_cert_path = str(cert_path)
    profile.client_key_path = str(key_path)

    all_profiles = _read_raw(_credentials_path())
    all_profiles[name] = asdict(profile)
    _write_raw(_credentials_path(), all_profiles)


def load_profile(name: str = DEFAULT_PROFILE) -> Profile:
    all_profiles = _read_raw(_credentials_path())
    data = all_profiles.get(name)
    if data is None:
        raise CredentialsNotFoundError(
            f"No hay ningún perfil {name!r} guardado en {_credentials_path()} -- "
            "corré `arca-service-client login` primero, o pasá base_url/"
            "client_cert_path/client_key_path/api_key explícitos."
        )
    return Profile(**data)


def save_pending_signup(name: str, *, base_url: str, slug: str, key_pem: str) -> PendingSignup:
    """Guarda la clave privada de un `request-invite --con-csr` mientras se espera el
    certificado real -- separado de `credentials.json`/`save_profile` a propósito: no
    hay todavía ningún perfil utilizable, solo una clave huérfana esperando su par."""
    directory = config_dir()
    directory.mkdir(parents=True, exist_ok=True)
    _chmod_best_effort(directory, 0o700)

    key_path = directory / f"{name}.pending.key"
    key_path.write_text(key_pem)
    _chmod_best_effort(key_path, 0o600)

    pending = PendingSignup(base_url=base_url, slug=slug, key_path=str(key_path))
    all_pending = _read_raw(_pending_signups_path())
    all_pending[name] = asdict(pending)
    _write_raw(_pending_signups_path(), all_pending)
    return pending


def load_pending_signup(name: str = DEFAULT_PROFILE) -> PendingSignup | None:
    """`None` si no hay ninguna solicitud con CSR pendiente para ese perfil -- a
    diferencia de `load_profile`, no es un error propio de este módulo: `completar-signup`
    (ver cli.py) es quien decide qué mensaje mostrar según el caso."""
    all_pending = _read_raw(_pending_signups_path())
    data = all_pending.get(name)
    return PendingSignup(**data) if data is not None else None


def discard_pending_signup(name: str) -> None:
    """Borra el registro Y el archivo de clave -- llamalo después de completar el
    signup con éxito (`save_profile` ya copió esa misma clave al perfil final), para no
    dejar una clave privada huérfana en disco indefinidamente. No-op si no había
    ninguno guardado con ese nombre."""
    all_pending = _read_raw(_pending_signups_path())
    data = all_pending.pop(name, None)
    if data is None:
        return
    _write_raw(_pending_signups_path(), all_pending)
    Path(data["key_path"]).unlink(missing_ok=True)


def _read_raw(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def _write_raw(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
    _chmod_best_effort(path, 0o600)
