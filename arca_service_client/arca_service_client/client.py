"""arca_service_client.client — `ArcaServiceClient`, envoltorio fino sobre la API HTTP
de arca-service (tixenre/arca-service, stack Phoenix -- `arca_service_phx/`). Un método
por endpoint, verificado contra `lib/arca_service_phx_web/router.ex`/
`lib/arca_service_phx_web/controllers/*.ex`/`lib/arca_service_phx_web/schemas/*.ex`
reales de ese repo — no una capa de abstracción propia encima, para que quien lea el
router de Phoenix reconozca 1:1 cada llamada de acá.

Modelo Cliente/Plataforma (Fase 12 de arca-service): `external_ref` identifica un
`Cliente` (el CUIT/CUIL dueño real de la facturación) — nunca lo elegís vos, lo devuelve
`por_cuit()` la primera vez que onboardeás un CUIT. Todo lo demás en este cliente
(emisión, preview, credencial, etc.) actúa DESDE tu Plataforma SOBRE ese Cliente.

Auth: mTLS (certificado propio de tu Plataforma como integrador, emitido por la CA mTLS
de Cloudflare que arca-service exige delante — ver
`lib/arca_service_phx_web/plugs/plataforma_auth.ex` de ese repo) + API key como Bearer
token. Las dos son obligatorias del lado servidor; sin el cert mTLS la request ni
siquiera llega a la capa de auth de la API key."""

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
    ArcaServiceError,
    ArcaServiceServerError,
    BonificadoLimiteError,
    IdempotencyConflictError,
    NotFoundError,
    RateLimitedError,
    ServiceNotReadyError,
    ValidationError,
)
from .local_config import DEFAULT_PROFILE, load_profile
from .models import (
    BonificadoResult,
    CredencialResult,
    DiagnosticoResult,
    EmbedTokenResult,
    EmisionResult,
    GenerarCsrResult,
    LoteItemResult,
    OnboardingResult,
    PersonaArca,
    PreviewResult,
    PuntosVentaResult,
)

_TIMEOUT_SECONDS_DEFAULT = 30.0
LAYOUT_DEFAULT = "oficial"  # mismo default que `ArcaServicePhx.Arca.Render.layout_default/0`


@dataclass
class ArcaServiceClient:
    """`base_url`: raíz del servicio SIN el prefijo de versión (ej.
    `"https://arca.tudominio.com"`) — este cliente arma `{base_url}/api/v1` solo, para
    que un futuro `/api/v2` de arca-service sea un cambio de ESTE paquete, no de cada
    integrador.

    `client_cert_path`/`client_key_path`: rutas al certificado y clave privada de
    cliente para mTLS — el par que la CA mTLS de Cloudflare delante de arca-service
    emitió para TU Plataforma como integrador (ganche/inmo/rambla/...), no el
    certificado AFIP de ningún Cliente particular (ese es interno de arca-service,
    nunca sale de ahí).

    `api_key`: la API key de tu `Plataforma` en arca-service (Bearer token) — identifica
    QUIÉN sos, el mTLS ya identificó QUE SOS VOS.

    Los cuatro son OPCIONALES: sin pasar ninguno, `ArcaServiceClient()` carga el perfil
    guardado por `arca-service-client login` (ver `local_config.py`) -- pensado para
    desarrollo local, no para producción (un container no tiene "tu" `~/.config"; ahí
    seguí pasando los cuatro explícitos, desde env vars/tu secret manager, como siempre).
    Pasar CUALQUIERA de los cuatro explícito salta la carga del perfil para ESE campo
    puntual -- `ArcaServiceClient(api_key="...")` sigue leyendo `base_url`/certs del
    perfil guardado, por ejemplo. `profile`: qué perfil usar si hace falta cargar uno
    (`"default"` si tenés uno solo).

    Se puede usar como context manager (`with ArcaServiceClient(...) as c:`) para cerrar
    la conexión sola, o llamar `.close()` a mano."""

    base_url: str | None = None
    client_cert_path: str | None = None
    client_key_path: str | None = None
    api_key: str | None = None
    timeout: float = _TIMEOUT_SECONDS_DEFAULT
    profile: str = DEFAULT_PROFILE

    def __post_init__(self) -> None:
        self._resolve_credentials()
        # Los cuatro son `str | None` en la firma (para aceptar `ArcaServiceClient()`
        # sin argumentos) pero `_resolve_credentials` garantiza que ninguno siga en
        # `None` al volver -- si no puede completarlos, levanta `CredentialsNotFoundError`
        # antes de esto. Los asserts son para mypy (no puede seguir esa garantía a
        # través de una llamada a método), no una validación real.
        assert self.base_url is not None
        assert self.client_cert_path is not None
        assert self.client_key_path is not None
        assert self.api_key is not None

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

    def _resolve_credentials(self) -> None:
        """Completa cualquier campo faltante desde el perfil guardado por
        `arca-service-client login` -- ver `local_config.py`. Si los cuatro ya vinieron
        explícitos, no toca el perfil (ni siquiera intenta leerlo: no hace falta que
        exista uno guardado). `load_profile` levanta `CredentialsNotFoundError` si hace
        falta uno y no hay ninguno -- eso ya deja los cuatro completos o no vuelve."""
        if self.base_url and self.client_cert_path and self.client_key_path and self.api_key:
            return

        stored = load_profile(self.profile)
        self.base_url = self.base_url or stored.base_url
        self.client_cert_path = self.client_cert_path or stored.client_cert_path
        self.client_key_path = self.client_key_path or stored.client_key_path
        self.api_key = self.api_key or stored.api_key

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> ArcaServiceClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Cliente — onboarding por CUIT + vínculo con tu Plataforma (Fase 12).
    # SIEMPRE el primer llamado: todo lo demás necesita el `external_ref`
    # que devuelve `por_cuit`.
    # ------------------------------------------------------------------

    def por_cuit(self, cuit: str) -> OnboardingResult:
        """Idempotente en dos sentidos (ver
        `ArcaServicePhx.Clientes.get_or_create_por_cuit/2`): un CUIT nuevo crea el
        `Cliente`; uno ya onboardeado por OTRA Plataforma se reusa, creando (o
        reactivando) solo TU vínculo con él — nunca un segundo `Cliente` para el mismo
        CUIT. Llamalo de nuevo con el mismo CUIT las veces que haga falta: nunca duplica
        nada, siempre devuelve el mismo `external_ref`."""
        resp = self._http.post("/clientes/por-cuit", json={"cuit": cuit})
        _raise_for_status(resp)
        return OnboardingResult._from_json(resp.json())

    def set_bonificado(self, external_ref: str, bonificado: bool) -> BonificadoResult:
        """Togglea si este Cliente, usado A TRAVÉS de TU Plataforma, queda exento de
        pagar su propia suscripción a arca-service (ver el plan de arca-service,
        "Bonificación cruzada") — nunca afecta el vínculo del Cliente con NINGUNA otra
        Plataforma. Sujeto al circuit-breaker de seguridad del lado servidor: activar
        (`bonificado=True`) un vínculo nuevo puede levantar `BonificadoLimiteError` (409)
        si tu Plataforma llegó al límite que arca-service te tiene configurado —
        desactivar (`bonificado=False`) nunca lo levanta."""
        resp = self._http.put(
            f"/clientes/{external_ref}/bonificado", json={"bonificado": bonificado}
        )
        _raise_for_status(resp, conflict_error=BonificadoLimiteError)
        return BonificadoResult._from_json(resp.json())

    # ------------------------------------------------------------------
    # Onboarding de credencial — dos caminos hacia una credencial AFIP para
    # un Cliente ya onboardeado (ver
    # `lib/arca_service_phx_web/controllers/credencial_controller.ex` en
    # arca-service): sin cert todavía, `generar_csr` + `completar_credencial`;
    # con cert+clave propios, `importar_credencial`.
    # ------------------------------------------------------------------

    def generar_csr(
        self, external_ref: str, cuit: str, *, regenerar: bool = False
    ) -> GenerarCsrResult:
        resp = self._http.post(
            f"/clientes/{external_ref}/csr", json={"cuit": cuit, "regenerar": regenerar}
        )
        _raise_for_status(resp)
        return GenerarCsrResult._from_json(resp.json())

    def completar_credencial(
        self, external_ref: str, cert_pem: str, *, point_of_sale: int = 0
    ) -> CredencialResult:
        resp = self._http.post(
            f"/clientes/{external_ref}/credencial/completar",
            json={"cert_pem": cert_pem, "point_of_sale": point_of_sale},
        )
        _raise_for_status(resp)
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
        _raise_for_status(pub_resp)
        public_key_pem = pub_resp.json()["public_key_pem"]

        secreto = {"key_pem": key_pem, "key_password": key_password}
        sealed = seal(json.dumps(secreto).encode(), public_key_pem.encode())

        resp = self._http.post(
            f"/clientes/{external_ref}/credencial/importar",
            json={
                "cuit": cuit,
                "cert_pem": cert_pem,
                "point_of_sale": point_of_sale,
                "sealed": sealed,
            },
        )
        _raise_for_status(resp)
        return CredencialResult._from_json(resp.json())

    def diagnosticar_credencial(self, external_ref: str) -> DiagnosticoResult:
        resp = self._http.post(f"/clientes/{external_ref}/credencial/diagnostico")
        _raise_for_status(resp)
        return DiagnosticoResult._from_json(resp.json())

    def listar_puntos_de_venta(self, external_ref: str) -> PuntosVentaResult:
        """Requiere que la credencial ya exista (`completar_credencial`/
        `importar_credencial` primero) — devuelve `NotFoundError` si no."""
        resp = self._http.get(f"/clientes/{external_ref}/credencial/puntos-venta")
        _raise_for_status(resp)
        return PuntosVentaResult._from_json(resp.json())

    # ------------------------------------------------------------------
    # Padrón
    # ------------------------------------------------------------------

    def consultar_padron(self, external_ref: str, cuit: str) -> PersonaArca:
        """Datos de padrón de CUALQUIER CUIT (ej. un receptor a facturar), autenticando
        con la credencial propia de `external_ref`."""
        resp = self._http.get(f"/clientes/{external_ref}/padron/{cuit}")
        _raise_for_status(resp)
        return PersonaArca._from_json(resp.json())

    # ------------------------------------------------------------------
    # Preview — sin efectos secundarios, no pide CAE ni persiste nada.
    # ------------------------------------------------------------------

    def preview_comprobante(
        self, external_ref: str, comprobante: ComprobanteInput
    ) -> PreviewResult:
        resp = self._http.post(
            f"/clientes/{external_ref}/comprobantes/preview", json=comprobante.to_payload()
        )
        _raise_for_status(resp)
        return PreviewResult._from_json(resp.json())

    def preview_nota_credito(
        self, external_ref: str, nota_credito: ComprobanteInput
    ) -> PreviewResult:
        """`nota_credito.comprobante_asociado` es obligatorio del lado servidor — pasalo
        seteado en el `ComprobanteInput` (ver su docstring)."""
        resp = self._http.post(
            f"/clientes/{external_ref}/notas-credito/preview", json=nota_credito.to_payload()
        )
        _raise_for_status(resp)
        return PreviewResult._from_json(resp.json())

    def preview_nota_debito(
        self, external_ref: str, nota_debito: ComprobanteInput
    ) -> PreviewResult:
        """Igual que `preview_nota_credito` — `nota_debito.comprobante_asociado` obligatorio."""
        resp = self._http.post(
            f"/clientes/{external_ref}/notas-debito/preview", json=nota_debito.to_payload()
        )
        _raise_for_status(resp)
        return PreviewResult._from_json(resp.json())

    # ------------------------------------------------------------------
    # Emisión — asincrónica: responde `pending` de inmediato, el resultado
    # real llega por polling (`get_comprobante`) y/o webhook.
    # ------------------------------------------------------------------

    def emitir_comprobante(self, external_ref: str, comprobante: ComprobanteInput) -> EmisionResult:
        resp = self._http.post(
            f"/clientes/{external_ref}/comprobantes", json=comprobante.to_payload()
        )
        _raise_for_status(resp)
        return EmisionResult._from_json(resp.json())

    def emitir_nota_credito(
        self, external_ref: str, nota_credito: ComprobanteInput
    ) -> EmisionResult:
        resp = self._http.post(
            f"/clientes/{external_ref}/notas-credito", json=nota_credito.to_payload()
        )
        _raise_for_status(resp)
        return EmisionResult._from_json(resp.json())

    def emitir_nota_debito(self, external_ref: str, nota_debito: ComprobanteInput) -> EmisionResult:
        resp = self._http.post(
            f"/clientes/{external_ref}/notas-debito", json=nota_debito.to_payload()
        )
        _raise_for_status(resp)
        return EmisionResult._from_json(resp.json())

    def get_comprobante(self, external_ref: str, idempotency_key: str) -> EmisionResult:
        resp = self._http.get(f"/clientes/{external_ref}/comprobantes/{idempotency_key}")
        _raise_for_status(resp)
        return EmisionResult._from_json(resp.json())

    # ------------------------------------------------------------------
    # Lote — colapsa N comprobantes en un solo request HTTP (crear las filas
    # es rápido, nunca toca AFIP acá tampoco: el resultado real llega igual
    # por polling/webhook, uno por ítem). SIEMPRE 200 con el resultado de
    # CADA ítem adentro (`LoteItemResult.ok`/`.error`) — un ítem con
    # `idempotency_key` en conflicto o payload inválido no aborta a los
    # demás, así que esto NUNCA levanta `IdempotencyConflictError`/
    # `ValidationError` por un ítem puntual (sí por el lote entero: más de
    # 200 ítems, o falta el campo — eso sigue siendo un 422 real, `_raise_for_status`
    # lo cubre igual).
    # ------------------------------------------------------------------

    def emitir_lote_comprobantes(
        self, external_ref: str, comprobantes: list[ComprobanteInput]
    ) -> list[LoteItemResult]:
        resp = self._http.post(
            f"/clientes/{external_ref}/comprobantes/lote",
            json={"comprobantes": [c.to_payload() for c in comprobantes]},
        )
        _raise_for_status(resp)
        return [LoteItemResult._from_json(item) for item in resp.json()]

    def emitir_lote_notas_credito(
        self, external_ref: str, notas_credito: list[ComprobanteInput]
    ) -> list[LoteItemResult]:
        resp = self._http.post(
            f"/clientes/{external_ref}/notas-credito/lote",
            json={"notas_credito": [n.to_payload() for n in notas_credito]},
        )
        _raise_for_status(resp)
        return [LoteItemResult._from_json(item) for item in resp.json()]

    def emitir_lote_notas_debito(
        self, external_ref: str, notas_debito: list[ComprobanteInput]
    ) -> list[LoteItemResult]:
        resp = self._http.post(
            f"/clientes/{external_ref}/notas-debito/lote",
            json={"notas_debito": [n.to_payload() for n in notas_debito]},
        )
        _raise_for_status(resp)
        return [LoteItemResult._from_json(item) for item in resp.json()]

    # ------------------------------------------------------------------
    # Webhooks
    # ------------------------------------------------------------------

    def reenviar_webhook(self, external_ref: str, idempotency_key: str) -> EmisionResult:
        """Reenvío a pedido — para cuando ya se agotaron los reintentos automáticos (ver
        `EmisionResult.webhook_delivered`/`.webhook_last_error`) o tu endpoint propio
        estuvo caído y se perdió la notificación original."""
        resp = self._http.post(
            f"/clientes/{external_ref}/comprobantes/{idempotency_key}/webhook/reenviar"
        )
        _raise_for_status(resp)
        return EmisionResult._from_json(resp.json())

    # ------------------------------------------------------------------
    # Documento renderizado — solo tiene sentido una vez `estado == "issued"`
    # (antes, arca-service igual intenta renderizar con los datos que haya).
    # ------------------------------------------------------------------

    def get_comprobante_html(
        self, external_ref: str, idempotency_key: str, *, layout: str = LAYOUT_DEFAULT
    ) -> str:
        resp = self._http.get(
            f"/clientes/{external_ref}/comprobantes/{idempotency_key}/comprobante.html",
            params={"layout": layout},
        )
        _raise_for_status(resp)
        return resp.text

    def get_comprobante_pdf(
        self, external_ref: str, idempotency_key: str, *, layout: str = LAYOUT_DEFAULT
    ) -> bytes:
        resp = self._http.get(
            f"/clientes/{external_ref}/comprobantes/{idempotency_key}/comprobante.pdf",
            params={"layout": layout},
        )
        _raise_for_status(resp)
        return resp.content

    def get_comprobante_imagen(
        self, external_ref: str, idempotency_key: str, *, layout: str = LAYOUT_DEFAULT
    ) -> bytes:
        resp = self._http.get(
            f"/clientes/{external_ref}/comprobantes/{idempotency_key}/comprobante.imagen",
            params={"layout": layout},
        )
        _raise_for_status(resp)
        return resp.content

    # ------------------------------------------------------------------
    # Vista embebible (iframe) -- complementa a get_comprobante_html/_pdf,
    # no los reemplaza: si TU backend ya tiene al usuario logueado, es más
    # simple llamar get_comprobante_html/_pdf del lado servidor y servir
    # ESE resultado como parte de tu propia página -- esto es para un link
    # público/compartible (o cuando el browser tiene que pegarle directo a
    # arca-service sin pasar por tu backend), ver el README.
    # ------------------------------------------------------------------

    def crear_embed_token(self, external_ref: str, idempotency_key: str) -> EmbedTokenResult:
        """`embed_url` sirve el HTML del comprobante SIN mTLS/API key -- listo para
        `<iframe src="...">`. Vale hasta `expires_at` (30 min por default del lado
        servidor); no hay forma de revocar un token puntual antes de que venza (mismo
        trade-off que un link de Stripe: la ventana corta ES el control)."""
        resp = self._http.post(
            f"/clientes/{external_ref}/comprobantes/{idempotency_key}/embed-token"
        )
        _raise_for_status(resp)
        return EmbedTokenResult._from_json(resp.json())


def _raise_for_status(
    resp: httpx.Response, *, conflict_error: type[ArcaServiceError] = IdempotencyConflictError
) -> None:
    """Traduce un `resp` con status >= 400 a `arca_service_client.exceptions`. Función de
    MÓDULO (no un método) a propósito -- no usa `self` para nada, y
    `AsyncArcaServiceClient` (`async_client.py`) la reusa tal cual: el mapeo status ->
    excepción es IDÉNTICO entre el cliente sync y el async, la única diferencia real
    entre los dos es el transporte (`httpx.Client` vs `httpx.AsyncClient`), no esto.

    Fallas de TRANSPORTE (timeout, conexión rechazada, DNS, TLS) NO se envuelven acá --
    se dejan propagar como las excepciones nativas de httpx
    (`httpx.TimeoutException`/`httpx.ConnectError`/etc.) — mezclar "el servidor
    respondió que no" con "ni pudimos preguntarle" perdería justo la distinción que hace
    útil tener excepciones tipadas.

    `conflict_error`: qué tipo levantar en un 409 — por default
    `IdempotencyConflictError` (el caso general, casi todo el resto de la API), pero
    `set_bonificado` pasa `BonificadoLimiteError` porque SU 409 es un tipo de conflicto
    totalmente distinto (circuit-breaker, no idempotencia) y confundir los dos tipos
    rompería a cualquier caller que discrimine por subtipo (ver exceptions.py)."""
    if resp.status_code < 400:
        return
    detail = _extraer_detail(resp)
    if resp.status_code == 404:
        raise NotFoundError(detail, status_code=404, response=resp)
    if resp.status_code == 409:
        raise conflict_error(detail, status_code=409, response=resp)
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


def _extraer_detail(resp: httpx.Response) -> str:
    """El body de error de arca-service siempre es `{"detail": "string"}` (ver
    `lib/arca_service_phx_web/controllers/fallback_controller.ex` de ese repo -- todo
    error pasa por ahí, INCLUSO un changeset de Ecto inválido, que por default trae
    errores por campo; arca-service lo aplana a un único string antes de responder). Si
    algún día no lo fuera (respuesta corrupta, proxy intermedio devolviendo HTML de
    error), no rompe acá — cae al texto crudo en vez de un `KeyError`/`JSONDecodeError`
    que ocultaría el error real detrás de OTRO error."""
    try:
        data = resp.json()
        if isinstance(data, dict) and "detail" in data:
            return str(data["detail"])
    except ValueError:
        pass
    return resp.text
