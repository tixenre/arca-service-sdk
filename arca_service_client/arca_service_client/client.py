"""arca_service_client.client — `ArcaServiceClient`, envoltorio fino sobre la API HTTP
de arca-service (tixenre/arca-service). Un método por endpoint, verificado contra
`apps/arca/api.py`/`apps/arca/schemas.py` reales de ese repo — no una capa de abstracción
propia encima, para que quien lea `apps/arca/api.py` reconozca 1:1 cada llamada de acá.

Auth: mTLS (certificado de cliente propio del producto, emitido por la CA mTLS de
Cloudflare que arca-service exige delante — ver `apps/clients/auth.py` de ese repo) +
API key como Bearer token. Las dos son obligatorias del lado servidor; sin el cert mTLS
la request ni siquiera llega a la capa de auth de la API key."""

from __future__ import annotations

import json
import ssl
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import httpx

    from .models import ComprobanteInput

import httpx as _httpx

from .crypto import seal
from .exceptions import (
    AfipUnavailableError,
    ArcaServiceServerError,
    IdempotencyConflictError,
    NotFoundError,
    RateLimitedError,
    ServiceNotReadyError,
    ValidationError,
)
from .models import (
    CredencialResult,
    DiagnosticoResult,
    EmisionResult,
    GenerarCsrResult,
    PersonaArca,
    PreviewResult,
    PuntosVentaResult,
)

_TIMEOUT_SECONDS_DEFAULT = 30.0
LAYOUT_DEFAULT = "oficial"  # mismo default que `apps/arca/render.py::LAYOUT_DEFAULT`


@dataclass
class ArcaServiceClient:
    """`base_url`: raíz del servicio SIN el prefijo de versión (ej.
    `"https://arca.tudominio.com"`) — este cliente arma `{base_url}/api/v1` solo, para
    que un futuro `/api/v2` de arca-service sea un cambio de ESTE paquete, no de cada
    integrador.

    `client_cert_path`/`client_key_path`: rutas al certificado y clave privada de
    cliente para mTLS — el par que la CA mTLS de Cloudflare delante de arca-service
    emitió para TU producto (ganche/inmo/rambla/...), no el certificado AFIP de ninguna
    org particular (ese es interno de arca-service, nunca sale de ahí).

    `api_key`: la API key de tu `Client` en arca-service (Bearer token) — identifica
    QUIÉN sos, el mTLS ya identificó QUE SOS VOS.

    Se puede usar como context manager (`with ArcaServiceClient(...) as c:`) para cerrar
    la conexión sola, o llamar `.close()` a mano."""

    base_url: str
    client_cert_path: str
    client_key_path: str
    api_key: str
    timeout: float = _TIMEOUT_SECONDS_DEFAULT

    def __post_init__(self) -> None:
        # `verify=<SSLContext>` + `load_cert_chain`, no el parámetro `cert=` de httpx
        # (deprecado desde 0.28 — sigue andando pero emite un warning en cada
        # construcción; esto es lo que la propia librería recomienda en su lugar).
        ssl_context = ssl.create_default_context()
        ssl_context.load_cert_chain(certfile=self.client_cert_path, keyfile=self.client_key_path)
        self._http = _httpx.Client(
            base_url=f"{self.base_url.rstrip('/')}/api/v1",
            verify=ssl_context,
            headers={"Authorization": f"Bearer {self.api_key}"},
            timeout=self.timeout,
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> ArcaServiceClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Transporte — traduce status >= 400 a `arca_service_client.exceptions`.
    # Fallas de TRANSPORTE (timeout, conexión rechazada, DNS, TLS) NO se
    # envuelven acá: se dejan propagar como las excepciones nativas de httpx
    # (`httpx.TimeoutException`, `httpx.ConnectError`, etc.) — mezclar "el
    # servidor respondió que no" con "ni pudimos preguntarle" perdería
    # justo la distinción que hace útil tener excepciones tipadas.
    # ------------------------------------------------------------------

    def _raise_for_status(self, resp: httpx.Response) -> None:
        if resp.status_code < 400:
            return
        detail = _extraer_detail(resp)
        if resp.status_code == 404:
            raise NotFoundError(detail, status_code=404, response=resp)
        if resp.status_code == 409:
            raise IdempotencyConflictError(detail, status_code=409, response=resp)
        if resp.status_code == 422:
            raise ValidationError(detail, status_code=422, response=resp)
        if resp.status_code == 429:
            retry_after = resp.headers.get("Retry-After")
            raise RateLimitedError(
                detail,
                status_code=429,
                response=resp,
                retry_after=int(retry_after) if retry_after is not None else None,
            )
        if resp.status_code == 502:
            raise AfipUnavailableError(detail, status_code=502, response=resp)
        if resp.status_code == 503:
            raise ServiceNotReadyError(detail, status_code=503, response=resp)
        raise ArcaServiceServerError(detail, status_code=resp.status_code, response=resp)

    # ------------------------------------------------------------------
    # Onboarding — dos caminos hacia una credencial (ver docstring de
    # `apps/arca/api.py` en arca-service): sin cert todavía, `generar_csr` +
    # `completar_credencial`; con cert+clave propios, `importar_credencial`.
    # ------------------------------------------------------------------

    def generar_csr(
        self, external_ref: str, cuit: str, *, regenerar: bool = False
    ) -> GenerarCsrResult:
        resp = self._http.post(
            f"/orgs/{external_ref}/csr", json={"cuit": cuit, "regenerar": regenerar}
        )
        self._raise_for_status(resp)
        return GenerarCsrResult._from_json(resp.json())

    def completar_credencial(
        self, external_ref: str, cert_pem: str, *, point_of_sale: int = 0
    ) -> CredencialResult:
        resp = self._http.post(
            f"/orgs/{external_ref}/credencial/completar",
            json={"cert_pem": cert_pem, "point_of_sale": point_of_sale},
        )
        self._raise_for_status(resp)
        return CredencialResult._from_json(resp.json())

    def importar_credencial(
        self,
        external_ref: str,
        cuit: str,
        cert_pem: str,
        key_pem: str,
        *,
        key_password: str | None = None,
        point_of_sale: int = 0,
    ) -> CredencialResult:
        """Sella `key_pem`/`key_password` con la clave pública vigente de arca-service
        (`GET /envelope/clave-publica`) antes de mandarlos — la clave privada AFIP nunca
        viaja en claro (ver `crypto.py::seal`). `cert_pem`/`cuit`/`point_of_sale` no son
        secretos, viajan tal cual."""
        pub_resp = self._http.get("/envelope/clave-publica")
        self._raise_for_status(pub_resp)
        public_key_pem = pub_resp.json()["public_key_pem"]

        secreto = {"key_pem": key_pem, "key_password": key_password}
        sealed = seal(json.dumps(secreto).encode(), public_key_pem.encode())

        resp = self._http.post(
            f"/orgs/{external_ref}/credencial/importar",
            json={
                "cuit": cuit,
                "cert_pem": cert_pem,
                "point_of_sale": point_of_sale,
                "sealed": sealed,
            },
        )
        self._raise_for_status(resp)
        return CredencialResult._from_json(resp.json())

    def diagnosticar_credencial(self, external_ref: str) -> DiagnosticoResult:
        resp = self._http.post(f"/orgs/{external_ref}/credencial/diagnostico")
        self._raise_for_status(resp)
        return DiagnosticoResult._from_json(resp.json())

    def listar_puntos_de_venta(self, external_ref: str) -> PuntosVentaResult:
        """Requiere que la credencial ya exista (`completar_credencial`/
        `importar_credencial` primero) — devuelve `NotFoundError` si no."""
        resp = self._http.get(f"/orgs/{external_ref}/credencial/puntos-venta")
        self._raise_for_status(resp)
        return PuntosVentaResult._from_json(resp.json())

    # ------------------------------------------------------------------
    # Padrón
    # ------------------------------------------------------------------

    def consultar_padron(self, external_ref: str, cuit: str) -> PersonaArca:
        """Datos de padrón de CUALQUIER CUIT (ej. un receptor a facturar), autenticando
        con la credencial propia de `external_ref`."""
        resp = self._http.get(f"/orgs/{external_ref}/padron/{cuit}")
        self._raise_for_status(resp)
        return PersonaArca._from_json(resp.json())

    # ------------------------------------------------------------------
    # Preview — sin efectos secundarios, no pide CAE ni persiste nada.
    # ------------------------------------------------------------------

    def preview_comprobante(
        self, external_ref: str, comprobante: ComprobanteInput
    ) -> PreviewResult:
        resp = self._http.post(
            f"/orgs/{external_ref}/comprobantes/preview", json=comprobante.to_payload()
        )
        self._raise_for_status(resp)
        return PreviewResult._from_json(resp.json())

    def preview_nota_credito(
        self, external_ref: str, nota_credito: ComprobanteInput
    ) -> PreviewResult:
        """`nota_credito.comprobante_asociado` es obligatorio del lado servidor — pasalo
        seteado en el `ComprobanteInput` (ver su docstring)."""
        resp = self._http.post(
            f"/orgs/{external_ref}/notas-credito/preview", json=nota_credito.to_payload()
        )
        self._raise_for_status(resp)
        return PreviewResult._from_json(resp.json())

    # ------------------------------------------------------------------
    # Emisión — asincrónica: responde `pending` de inmediato, el resultado
    # real llega por polling (`get_comprobante`) y/o webhook.
    # ------------------------------------------------------------------

    def emitir_comprobante(self, external_ref: str, comprobante: ComprobanteInput) -> EmisionResult:
        resp = self._http.post(f"/orgs/{external_ref}/comprobantes", json=comprobante.to_payload())
        self._raise_for_status(resp)
        return EmisionResult._from_json(resp.json())

    def emitir_nota_credito(
        self, external_ref: str, nota_credito: ComprobanteInput
    ) -> EmisionResult:
        resp = self._http.post(
            f"/orgs/{external_ref}/notas-credito", json=nota_credito.to_payload()
        )
        self._raise_for_status(resp)
        return EmisionResult._from_json(resp.json())

    def get_comprobante(self, external_ref: str, idempotency_key: str) -> EmisionResult:
        resp = self._http.get(f"/orgs/{external_ref}/comprobantes/{idempotency_key}")
        self._raise_for_status(resp)
        return EmisionResult._from_json(resp.json())

    # ------------------------------------------------------------------
    # Documento renderizado — solo tiene sentido una vez `estado == "issued"`
    # (antes, arca-service igual intenta renderizar con los datos que haya).
    # ------------------------------------------------------------------

    def get_comprobante_html(
        self, external_ref: str, idempotency_key: str, *, layout: str = LAYOUT_DEFAULT
    ) -> str:
        resp = self._http.get(
            f"/orgs/{external_ref}/comprobantes/{idempotency_key}/comprobante.html",
            params={"layout": layout},
        )
        self._raise_for_status(resp)
        return resp.text

    def get_comprobante_pdf(
        self, external_ref: str, idempotency_key: str, *, layout: str = LAYOUT_DEFAULT
    ) -> bytes:
        resp = self._http.get(
            f"/orgs/{external_ref}/comprobantes/{idempotency_key}/comprobante.pdf",
            params={"layout": layout},
        )
        self._raise_for_status(resp)
        return resp.content

    def get_comprobante_imagen(
        self, external_ref: str, idempotency_key: str, *, layout: str = LAYOUT_DEFAULT
    ) -> bytes:
        resp = self._http.get(
            f"/orgs/{external_ref}/comprobantes/{idempotency_key}/comprobante.imagen",
            params={"layout": layout},
        )
        self._raise_for_status(resp)
        return resp.content


def _extraer_detail(resp: httpx.Response) -> str:
    """El body de error de arca-service siempre es `{"detail": "string"}` (ver
    `config/api.py` de ese repo — todo exception handler devuelve ese shape, INCLUSO el
    de `ValidationError` de Ninja, que por default manda una lista de objetos; arca-service
    lo aplana a string antes de responder). Si algún día no lo fuera (respuesta
    corrupta, proxy intermedio devolviendo HTML de error), no rompe acá — cae al texto
    crudo en vez de un `KeyError`/`JSONDecodeError` que ocultaría el error real detrás de
    OTRO error."""
    try:
        data = resp.json()
        if isinstance(data, dict) and "detail" in data:
            return str(data["detail"])
    except ValueError:
        pass
    return resp.text
