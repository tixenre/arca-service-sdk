"""arca_service_client.local_config — perfiles guardados por `arca-service-client login`
(ver `cli.py`) para que `ArcaServiceClient()`/`AsyncArcaServiceClient()` se puedan
instanciar SIN argumentos en desarrollo local — mismo patrón que `~/.aws/credentials`
(AWS CLI) o `~/.config/gh/hosts.yml` (`gh` CLI): un comando de login una sola vez, el SDK
lee solo de ahí después.

Deliberadamente NO pensado para producción -- un container no tiene "tu" home
directory, y no debería depender de que alguien haya corrido `login` a mano ahí. En
producción seguí pasando `base_url`/`client_cert_path`/`client_key_path`/`api_key`
explícitos (env vars, tu secret manager) — exactamente como ya se documentaba antes de
que este módulo existiera. Este archivo resuelve la fricción de "tengo que arrancar a
desarrollar contra arca-service ahora mismo", no cambia nada del contrato de producción.

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


def config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "arca-service"


def _credentials_path() -> Path:
    return config_dir() / "credentials.json"


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

    all_profiles = _read_raw()
    all_profiles[name] = asdict(profile)
    _write_raw(all_profiles)


def load_profile(name: str = DEFAULT_PROFILE) -> Profile:
    all_profiles = _read_raw()
    data = all_profiles.get(name)
    if data is None:
        raise CredentialsNotFoundError(
            f"No hay ningún perfil {name!r} guardado en {_credentials_path()} -- "
            "corré `arca-service-client login` primero, o pasá base_url/"
            "client_cert_path/client_key_path/api_key explícitos."
        )
    return Profile(**data)


def _read_raw() -> dict:
    path = _credentials_path()
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def _write_raw(all_profiles: dict) -> None:
    path = _credentials_path()
    path.write_text(json.dumps(all_profiles, indent=2, sort_keys=True) + "\n")
    _chmod_best_effort(path, 0o600)
