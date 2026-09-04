"""arca_service_client.client — `ArcaServiceClient`, envoltorio fino sobre la API HTTP
de arca-service. Un método por endpoint — no una capa de abstracción propia encima,
para que cada llamada de acá mapee 1:1 a un endpoint real de la API.

Modelo Cliente/Plataforma: `external_ref` identifica un `Cliente` (el CUIT/CUIL dueño
real de la facturación) — nunca lo elegís vos, lo devuelve `por_cuit()` la primera vez
que onboardeás un CUIT. Todo lo demás en este cliente (emisión, preview, credencial,
etc.) actúa DESDE tu Plataforma SOBRE ese Cliente.

Auth: mTLS (certificado propio de tu Plataforma como integrador, emitido por la CA mTLS
que arca-service exige delante) + API key como Bearer token. Las dos son obligatorias
del lado servidor; sin el cert mTLS la request ni siquiera llega a la capa de auth de
la API key."""

from __future__ import annotations

import json
import ssl
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import httpx

    from .models import ComprobanteInput, SesionEmbebidaInput

import httpx as _httpx

from .crypto import seal
from .exceptions import (
    AfipError,
    AfipErrorDetail,
    AfipRechazoError,
    AfipUnavailableError,
    ArcaServiceError,
    BonificadoLimiteError,
    ConfiguracionError,
    CredencialYaActivaError,
    CredentialsRejectedError,
    CsrYaExisteError,
    IdempotencyConflictError,
    InternoError,
    NotaExcedeComprobanteError,
    NotFoundError,
    PuntoVentaNoHabilitadoError,
    RateLimitedError,
    RequestError,
    ServicioNoDisponibleError,
)
from .local_config import DEFAULT_PROFILE, load_profile
from .models import (
    BonificadoResult,
    ConexionAfipEmbedTokenResult,
    CredencialResult,
    DiagnosticoResult,
    EmbedTokenResult,
    EmisionResult,
    FacturacionResult,
    GenerarCsrResult,
    ListaComprobantesResult,
    LoteItemResult,
    OnboardingResult,
    PersonaArca,
    PreviewResult,
    PuntosVentaResult,
    SesionEmbebidaResult,
)

_TIMEOUT_SECONDS_DEFAULT = 30.0
# Los tres layouts que aceptan los doce métodos que renderizan. `"oficial"` es el mismo
# default que usa el servidor si no se manda `layout`.
#
# `"simplificada"` es la única con límites: es una tarjeta chica y NO recorta lo que no
# entra -- devuelve 422 (`RequestError`) si el comprobante tiene más de 3 ítems, o si
# algún ítem no se puede resumir a "descripción + importe" sin perder nada (descripción
# de más de 40 caracteres, cantidad != 1, con bonificación, con detalle, o con una unidad
# de medida distinta de la default). Ver el README. Las otras dos no tienen límite.
LAYOUT_DEFAULT = "oficial"


class CredentialsInvalidError(Exception):
    """`client_cert_path`/`client_key_path` no forman un par válido -- certificado y
    clave no se corresponden, o alguno de los dos está corrompido/mal formado (típico
    después de copiarlos a mano hacia donde vayan a vivir: env vars, un gestor de
    secretos). NO es un `ArcaServiceError` -- pasa ANTES de que exista ningún request,
    nunca es una respuesta de arca-service. Sin este chequeo, el mismo problema recién
    aparece como un `ssl.SSLError` cuando se manda el primer request."""


@dataclass
class ArcaServiceClient:
    """`base_url`: raíz del servicio SIN el prefijo de versión (ej.
    `"https://arca.mancino.dev"`) — este cliente arma `{base_url}/api/v1` solo, para
    que un futuro `/api/v2` de arca-service sea un cambio de ESTE paquete, no de cada
    integrador.

    `client_cert_path`/`client_key_path`: rutas al certificado y clave privada de
    cliente para mTLS — el par que la CA mTLS delante de arca-service emitió para TU
    Plataforma como integrador, no el certificado AFIP de ningún Cliente particular (ese
    es interno de arca-service, nunca sale de ahí). Si no forman un par válido, el
    constructor levanta `CredentialsInvalidError` de una — no hace falta mandar ningún
    request para enterarte.

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
        try:
            ssl_context.load_cert_chain(
                certfile=self.client_cert_path, keyfile=self.client_key_path
            )
        except ssl.SSLError as exc:
            # Mismo chequeo que ya hacían `completar-signup`/`import` en la CLI antes de
            # guardar nada -- acá hacía falta también, para quien arma el par a mano (ej.
            # copiándolo hacia un gestor de secretos) y nunca pasó por la CLI.
            raise CredentialsInvalidError(
                f"client_cert_path/client_key_path no forman un par válido: {exc}"
            ) from exc
        self._http = _httpx.Client(
            base_url=f"{self.base_url.rstrip('/')}/api/v1",
            verify=ssl_context,
            # `Accept` explícito y no el default de httpx (`*/*`) -- sin esto, un 404 de
            # ruta inexistente (o cualquier error servido antes de llegar a la capa JSON
            # de la API) vuelve como texto plano ("Not Found") en vez del sobre
            # `{"error": {...}}`, y `_parse_error_envelope` no tiene nada que parsear.
            # Comprobado contra producción, no una suposición.
            headers={"Accept": "application/json", "Authorization": f"Bearer {self.api_key}"},
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
    # Cliente — onboarding por CUIT + vínculo con tu Plataforma.
    # SIEMPRE el primer llamado: todo lo demás necesita el `external_ref`
    # que devuelve `por_cuit`.
    # ------------------------------------------------------------------

    def por_cuit(self, cuit: str) -> OnboardingResult:
        """Idempotente en dos sentidos: un CUIT nuevo crea el
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
        _raise_for_status(resp)
        return BonificadoResult._from_json(resp.json())

    def set_facturacion(
        self, external_ref: str, *, iibb: str | None = None, nombre_comercial: str | None = None
    ) -> FacturacionResult:
        """Configura el IIBB/nombre de fantasía de ESTE Cliente para el render de sus
        comprobantes — una sola vez, no en cada emisión. Razón social y domicilio no se
        aceptan acá: los trae el padrón de AFIP."""
        payload: dict = {}
        if iibb is not None:
            payload["iibb"] = iibb
        if nombre_comercial is not None:
            payload["nombre_comercial"] = nombre_comercial
        resp = self._http.put(f"/clientes/{external_ref}/facturacion", json=payload)
        _raise_for_status(resp)
        return FacturacionResult._from_json(resp.json())

    # ------------------------------------------------------------------
    # Onboarding de credencial — dos caminos hacia una credencial AFIP para
    # un Cliente ya onboardeado: sin cert todavía, `generar_csr` +
    # `completar_credencial`; con cert+clave propios, `importar_credencial`.
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
    # Conexión AFIP embebida (iframe) -- alternativa a generar_csr +
    # completar_credencial/importar_credencial de arriba: en vez de que TU
    # backend orqueste esos pasos, tu cliente final los completa él mismo
    # en un `<iframe>` servido por arca-service (mismo patrón "hosted page"
    # que crear_embed_token, más abajo, pero un flujo interactivo en vez de
    # una vista de solo lectura). Ver el README, "Conexión AFIP embebida
    # (iframe)".
    # ------------------------------------------------------------------

    def crear_conexion_afip_embed_token(self, external_ref: str) -> ConexionAfipEmbedTokenResult:
        """`embed_url` sirve `ConexionAfipLive` (arca-service) SIN mTLS/API key -- listo
        para `<iframe src="...">`. El flujo entero corre ahí (generar/subir el
        certificado, elegir cuál usar si ya tenía varios) sin que tu backend tenga que
        estar en el medio paso a paso. Cuando tu cliente final termina de conectar su
        cuenta, esa página manda `window.parent.postMessage({type:
        "arca:conexion_completa"}, "*")` -- escuchalo en tu frontend
        (`window.addEventListener("message", ...)`) para reaccionar (cerrar el iframe,
        refrescar tu propio estado, etc.) sin tener que hacer polling contra tu backend."""
        resp = self._http.post(f"/clientes/{external_ref}/conexion-afip/embed-token")
        _raise_for_status(resp)
        return ConexionAfipEmbedTokenResult._from_json(resp.json())

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
    # Preview renderizado — el `.html`/`.pdf`/`.imagen` de un preview, ANTES
    # de emitir (nada se persiste). Complementa a preview_comprobante/etc.
    # (que solo dan los importes): esto es para mostrarle a alguien cómo va
    # a quedar el comprobante antes de confirmar una acción fiscal
    # irreversible. `layout` va en el mismo body que el resto del
    # comprobante, no como query param.
    # ------------------------------------------------------------------

    def preview_comprobante_html(
        self, external_ref: str, comprobante: ComprobanteInput, *, layout: str = LAYOUT_DEFAULT
    ) -> str:
        resp = self._http.post(
            f"/clientes/{external_ref}/comprobantes/preview/comprobante.html",
            json={**comprobante.to_payload(), "layout": layout},
        )
        _raise_for_status(resp)
        return resp.text

    def preview_comprobante_pdf(
        self, external_ref: str, comprobante: ComprobanteInput, *, layout: str = LAYOUT_DEFAULT
    ) -> bytes:
        resp = self._http.post(
            f"/clientes/{external_ref}/comprobantes/preview/comprobante.pdf",
            json={**comprobante.to_payload(), "layout": layout},
        )
        _raise_for_status(resp)
        return resp.content

    def preview_comprobante_imagen(
        self, external_ref: str, comprobante: ComprobanteInput, *, layout: str = LAYOUT_DEFAULT
    ) -> bytes:
        resp = self._http.post(
            f"/clientes/{external_ref}/comprobantes/preview/comprobante.imagen",
            json={**comprobante.to_payload(), "layout": layout},
        )
        _raise_for_status(resp)
        return resp.content

    def preview_nota_credito_html(
        self, external_ref: str, nota_credito: ComprobanteInput, *, layout: str = LAYOUT_DEFAULT
    ) -> str:
        resp = self._http.post(
            f"/clientes/{external_ref}/notas-credito/preview/comprobante.html",
            json={**nota_credito.to_payload(), "layout": layout},
        )
        _raise_for_status(resp)
        return resp.text

    def preview_nota_credito_pdf(
        self, external_ref: str, nota_credito: ComprobanteInput, *, layout: str = LAYOUT_DEFAULT
    ) -> bytes:
        resp = self._http.post(
            f"/clientes/{external_ref}/notas-credito/preview/comprobante.pdf",
            json={**nota_credito.to_payload(), "layout": layout},
        )
        _raise_for_status(resp)
        return resp.content

    def preview_nota_credito_imagen(
        self, external_ref: str, nota_credito: ComprobanteInput, *, layout: str = LAYOUT_DEFAULT
    ) -> bytes:
        resp = self._http.post(
            f"/clientes/{external_ref}/notas-credito/preview/comprobante.imagen",
            json={**nota_credito.to_payload(), "layout": layout},
        )
        _raise_for_status(resp)
        return resp.content

    def preview_nota_debito_html(
        self, external_ref: str, nota_debito: ComprobanteInput, *, layout: str = LAYOUT_DEFAULT
    ) -> str:
        resp = self._http.post(
            f"/clientes/{external_ref}/notas-debito/preview/comprobante.html",
            json={**nota_debito.to_payload(), "layout": layout},
        )
        _raise_for_status(resp)
        return resp.text

    def preview_nota_debito_pdf(
        self, external_ref: str, nota_debito: ComprobanteInput, *, layout: str = LAYOUT_DEFAULT
    ) -> bytes:
        resp = self._http.post(
            f"/clientes/{external_ref}/notas-debito/preview/comprobante.pdf",
            json={**nota_debito.to_payload(), "layout": layout},
        )
        _raise_for_status(resp)
        return resp.content

    def preview_nota_debito_imagen(
        self, external_ref: str, nota_debito: ComprobanteInput, *, layout: str = LAYOUT_DEFAULT
    ) -> bytes:
        resp = self._http.post(
            f"/clientes/{external_ref}/notas-debito/preview/comprobante.imagen",
            json={**nota_debito.to_payload(), "layout": layout},
        )
        _raise_for_status(resp)
        return resp.content

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

    def listar_comprobantes(
        self,
        external_ref: str,
        *,
        estado: str | None = None,
        tipo: str | None = None,
        creado_desde: date | None = None,
        creado_hasta: date | None = None,
        receptor_cuit: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> ListaComprobantesResult:
        """Todos los que este Cliente tiene emitidos/pendientes/en error, más nuevo
        primero -- filtrable por `estado` (`"pending"`/`"issued"`/`"error"`), `tipo`
        (`"factura"`/`"nota_credito"`/`"nota_debito"`) y `receptor_cuit` (con guiones o
        pelado, sin exigir dígito verificador -- solo encuentra lo emitido con CUIT, un
        receptor por DNI o consumidor final nunca aparece filtrando así).
        `creado_desde`/`creado_hasta` filtran por cuándo se PIDIÓ la emisión, no por la
        fecha fiscal del comprobante. Sin resultados es una lista vacía, nunca un 404."""
        params: dict = {"limit": limit, "offset": offset}
        if estado is not None:
            params["estado"] = estado
        if tipo is not None:
            params["tipo"] = tipo
        if receptor_cuit is not None:
            params["receptor_cuit"] = receptor_cuit
        if creado_desde is not None:
            params["creado_desde"] = creado_desde.isoformat()
        if creado_hasta is not None:
            params["creado_hasta"] = creado_hasta.isoformat()
        resp = self._http.get(f"/clientes/{external_ref}/comprobantes", params=params)
        _raise_for_status(resp)
        return ListaComprobantesResult._from_json(resp.json())

    # ------------------------------------------------------------------
    # Sesión embebida (iframe) — puerta de entrada ALTERNATIVA a
    # emitir_comprobante/emitir_nota_credito/emitir_nota_debito de arriba,
    # para cuando tu Plataforma sabe cuánto facturar pero no a quién: el
    # comprador completa el receptor él mismo en un <iframe> que sirve
    # arca-service. Mismo body que ComprobanteInput/emitir_* pero SIN
    # receptor (ver SesionEmbebidaInput) -- no reemplaza a los métodos de
    # arriba, es una puerta más. El resto del comprobante (moneda, importes,
    # items) se valida en ESTE request, así que un ítem mal armado da error
    # acá y no media hora después con alguien mirando un iframe que no carga.
    # ------------------------------------------------------------------

    def crear_sesion_embebida_comprobante(
        self, external_ref: str, comprobante: SesionEmbebidaInput
    ) -> SesionEmbebidaResult:
        resp = self._http.post(
            f"/clientes/{external_ref}/comprobantes/sesion-embebida",
            json=comprobante.to_payload(),
        )
        _raise_for_status(resp)
        return SesionEmbebidaResult._from_json(resp.json())

    def crear_sesion_embebida_nota_credito(
        self, external_ref: str, nota_credito: SesionEmbebidaInput
    ) -> SesionEmbebidaResult:
        """`nota_credito.comprobante_asociado` es obligatorio del lado servidor, igual
        que en `emitir_nota_credito` -- ver el docstring de `ComprobanteInput`."""
        resp = self._http.post(
            f"/clientes/{external_ref}/notas-credito/sesion-embebida",
            json=nota_credito.to_payload(),
        )
        _raise_for_status(resp)
        return SesionEmbebidaResult._from_json(resp.json())

    def crear_sesion_embebida_nota_debito(
        self, external_ref: str, nota_debito: SesionEmbebidaInput
    ) -> SesionEmbebidaResult:
        """Igual que `crear_sesion_embebida_nota_credito` -- `comprobante_asociado`
        obligatorio."""
        resp = self._http.post(
            f"/clientes/{external_ref}/notas-debito/sesion-embebida",
            json=nota_debito.to_payload(),
        )
        _raise_for_status(resp)
        return SesionEmbebidaResult._from_json(resp.json())

    # ------------------------------------------------------------------
    # Lote — colapsa N comprobantes en un solo request HTTP (crear las filas
    # es rápido, nunca toca AFIP acá tampoco: el resultado real llega igual
    # por polling/webhook, uno por ítem). SIEMPRE 200 con el resultado de
    # CADA ítem adentro (`LoteItemResult.ok`/`.error`) — un ítem con
    # `idempotency_key` en conflicto o payload inválido no aborta a los
    # demás, así que esto NUNCA levanta `IdempotencyConflictError`/
    # `RequestError` por un ítem puntual (sí por el lote entero: más de
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


# `code` -> excepción, solo para los que vale la pena distinguir sin obligar a mirar
# `.code` a mano (ver exceptions.py). Deliberadamente NO exhaustiva -- MIGRACION.md,
# punto 1: "la lista de code crece, los cuatro type no". Un `code` que no está acá cae en
# la excepción de su `.type` (_EXCEPCION_POR_TYPE, abajo), nunca en un catch-all sin
# tipar.
_EXCEPCION_POR_CODE: dict[str, type[ArcaServiceError]] = {
    "no_encontrado": NotFoundError,
    # Los dos rechazos de credencial: 401 (no te pudimos identificar) y 403 (el request
    # no entró por donde tiene que entrar). Comparten excepción porque comparten remedio
    # -- ver CredentialsRejectedError.
    "no_autenticado": CredentialsRejectedError,
    "origen_no_verificado": CredentialsRejectedError,
    "idempotency_key_reusada": IdempotencyConflictError,
    "csr_ya_existe": CsrYaExisteError,
    "credencial_ya_activa": CredencialYaActivaError,
    "limite_bonificados_alcanzado": BonificadoLimiteError,
    "rate_limit": RateLimitedError,
    "punto_venta_no_habilitado": PuntoVentaNoHabilitadoError,
    "nota_excede_comprobante": NotaExcedeComprobanteError,
    "afip_rechazo": AfipRechazoError,
    "afip_sin_respuesta": AfipUnavailableError,
    "afip_respuesta_ilegible": AfipUnavailableError,
    "servicio_no_disponible": ServicioNoDisponibleError,
}

# `type` -> excepción genérica, para cualquier `code` que _EXCEPCION_POR_CODE no
# reconozca. Estos CUATRO son la única parte de este mapeo que arca-service garantiza
# estable (ver exceptions.py) -- por eso el fallback si ni el `type` viniera reconocible
# (respuesta corrupta) es InternoError, no una excepción sin tipar.
_EXCEPCION_POR_TYPE: dict[str, type[ArcaServiceError]] = {
    "request": RequestError,
    "configuracion": ConfiguracionError,
    "afip": AfipError,
    "interno": InternoError,
}


@dataclass(frozen=True)
class _ErrorEnvelope:
    type: str
    code: str
    message: str
    param: str | None
    afip: tuple[AfipErrorDetail, ...] | None


def _parse_error_envelope(resp: httpx.Response) -> _ErrorEnvelope:
    """Lee `{"error": {"type", "code", "message", "param"?, "afip"?}}` -- el sobre único
    de toda la API (ver exceptions.py). Si el body no tiene esa forma (un proxy
    intermedio devolviendo texto/HTML, por ejemplo si alguna vez faltara el header
    `Accept: application/json` que este cliente siempre manda -- comprobado contra
    producción: sin ese header un 404 vuelve como texto plano), no rompe acá: cae a
    `type="interno"`/`code=""` con el texto crudo en `message`, en vez de un
    `KeyError`/`JSONDecodeError` que ocultaría el error real detrás de OTRO error."""
    try:
        error = resp.json()["error"]
        afip_bruto = error.get("afip")
        afip = (
            tuple(AfipErrorDetail(codigo=a["codigo"], mensaje=a["mensaje"]) for a in afip_bruto)
            if afip_bruto
            else None
        )
        return _ErrorEnvelope(
            type=error["type"],
            code=error["code"],
            message=error["message"],
            param=error.get("param"),
            afip=afip,
        )
    except (ValueError, KeyError, TypeError, AttributeError):
        return _ErrorEnvelope(type="interno", code="", message=resp.text, param=None, afip=None)


def _raise_for_status(resp: httpx.Response) -> None:
    """Traduce un `resp` con status >= 400 a `arca_service_client.exceptions`, ramificando
    por `error.code`/`error.type` -- nunca por `status_code` (varios `code` bien distintos
    pueden compartir status, ej. `idempotency_key_reusada`, `csr_ya_existe` y
    `limite_bonificados_alcanzado` son los tres 409; ver MIGRACION.md punto 1). Función de
    MÓDULO (no un método) a propósito -- no usa `self` para nada, y
    `AsyncArcaServiceClient` (`async_client.py`) la reusa tal cual: el mapeo es IDÉNTICO
    entre el cliente sync y el async, la única diferencia real entre los dos es el
    transporte (`httpx.Client` vs `httpx.AsyncClient`), no esto.

    Fallas de TRANSPORTE (timeout, conexión rechazada, DNS, TLS) NO se envuelven acá --
    se dejan propagar como las excepciones nativas de httpx
    (`httpx.TimeoutException`/`httpx.ConnectError`/etc.) — mezclar "el servidor
    respondió que no" con "ni pudimos preguntarle" perdería justo la distinción que hace
    útil tener excepciones tipadas."""
    if resp.status_code < 400:
        return

    envelope = _parse_error_envelope(resp)

    excepcion = _EXCEPCION_POR_CODE.get(envelope.code) or _EXCEPCION_POR_TYPE.get(
        envelope.type, InternoError
    )

    kwargs: dict = dict(
        type=envelope.type,
        code=envelope.code,
        message=envelope.message,
        status_code=resp.status_code,
        param=envelope.param,
        afip=envelope.afip,
        response=resp,
    )
    if issubclass(excepcion, RateLimitedError):
        retry_after = resp.headers.get("Retry-After")
        kwargs["retry_after"] = int(retry_after) if retry_after is not None else None
    raise excepcion(**kwargs)
