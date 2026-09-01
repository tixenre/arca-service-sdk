"""arca_service_client.async_client — `AsyncArcaServiceClient`, la MISMA API que
`ArcaServiceClient` (`client.py`) sobre `httpx.AsyncClient` en vez de `httpx.Client` —
para un consumidor async nativo (ej. FastAPI), sin tener que envolver un cliente sync
en `asyncio.to_thread` para no bloquear el event loop. Mismos métodos, mismos nombres,
mismo shape de retorno — la única
diferencia es `async def`/`await` en cada uno. El mapeo de errores
(`_raise_for_status`, ver `client.py`) es una función de módulo compartida entre los dos:
no hay NADA async-específico en decidir qué excepción tirar según el status code.

Deliberadamente un archivo aparte (no un flag/parámetro en `ArcaServiceClient`) — mismo
criterio que `httpx.Client`/`httpx.AsyncClient` en sí: mezclar los dos modos en una sola
clase obligaría a decidir en cada método si `await`-ear o no, y un integrador sync
importando este módulo arrastraría el mismo código igual."""

from __future__ import annotations

import json
import ssl
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING

if TYPE_CHECKING:

    from .models import ComprobanteInput, SesionEmbebidaInput

import httpx as _httpx

from .client import (
    _TIMEOUT_SECONDS_DEFAULT,
    LAYOUT_DEFAULT,
    CredentialsInvalidError,
    _raise_for_status,
)
from .crypto import seal
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


@dataclass
class AsyncArcaServiceClient:
    """Ver `ArcaServiceClient` (`client.py`) para el detalle de cada parámetro/método --
    es la misma documentación, esta clase no la repite. Se usa igual, con `await` en
    cada llamado:

        async with AsyncArcaServiceClient(...) as client:
            onboarding = await client.por_cuit("20301234563")
            emision = await client.emitir_comprobante(onboarding.external_ref, ...)

    `.aclose()` (no `.close()`) para cerrar la conexión a mano fuera de un `async with`
    -- mismo nombre que usa `httpx.AsyncClient` para lo mismo, a propósito: alguien que
    ya usó httpx async reconoce el método sin tener que mirar la doc.

    Los cuatro campos son opcionales, mismo criterio que `ArcaServiceClient` -- ver su
    docstring (`client.py`) para el detalle de cómo se resuelve el perfil guardado por
    `arca-service-client login` cuando no se pasan explícitos."""

    base_url: str | None = None
    client_cert_path: str | None = None
    client_key_path: str | None = None
    api_key: str | None = None
    timeout: float = _TIMEOUT_SECONDS_DEFAULT
    profile: str = DEFAULT_PROFILE

    def __post_init__(self) -> None:
        self._resolve_credentials()
        # Ver ArcaServiceClient.__post_init__ (client.py) -- mismos asserts, mismo
        # motivo (mypy no sigue la garantía de _resolve_credentials a través del
        # llamado a método).
        assert self.base_url is not None
        assert self.client_cert_path is not None
        assert self.client_key_path is not None
        assert self.api_key is not None

        # Construir un `httpx.AsyncClient` no requiere estar dentro de un event loop
        # (solo `.aclose()`/los métodos de request sí) -- por eso `__post_init__` puede
        # seguir siendo sync, igual que en `ArcaServiceClient`.
        ssl_context = ssl.create_default_context()
        try:
            ssl_context.load_cert_chain(
                certfile=self.client_cert_path, keyfile=self.client_key_path
            )
        except ssl.SSLError as exc:
            # Ver ArcaServiceClient.__post_init__ (client.py) -- mismo motivo.
            raise CredentialsInvalidError(
                f"client_cert_path/client_key_path no forman un par válido: {exc}"
            ) from exc
        self._http = _httpx.AsyncClient(
            base_url=f"{self.base_url.rstrip('/')}/api/v1",
            verify=ssl_context,
            # Ver ArcaServiceClient.__post_init__ (client.py) para el porqué de este
            # header explícito -- mismo motivo acá.
            headers={"Accept": "application/json", "Authorization": f"Bearer {self.api_key}"},
            timeout=self.timeout,
        )

    def _resolve_credentials(self) -> None:
        if self.base_url and self.client_cert_path and self.client_key_path and self.api_key:
            return  # los cuatro ya vinieron explícitos -- no toca el perfil guardado.

        stored = load_profile(self.profile)  # CredentialsNotFoundError si no hay ninguno.
        self.base_url = self.base_url or stored.base_url
        self.client_cert_path = self.client_cert_path or stored.client_cert_path
        self.client_key_path = self.client_key_path or stored.client_key_path
        self.api_key = self.api_key or stored.api_key

    async def aclose(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> AsyncArcaServiceClient:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    # ------------------------------------------------------------------
    # Cliente — onboarding por CUIT + vínculo con tu Plataforma.
    # ------------------------------------------------------------------

    async def por_cuit(self, cuit: str) -> OnboardingResult:
        resp = await self._http.post("/clientes/por-cuit", json={"cuit": cuit})
        _raise_for_status(resp)
        return OnboardingResult._from_json(resp.json())

    async def set_bonificado(self, external_ref: str, bonificado: bool) -> BonificadoResult:
        resp = await self._http.put(
            f"/clientes/{external_ref}/bonificado", json={"bonificado": bonificado}
        )
        _raise_for_status(resp)
        return BonificadoResult._from_json(resp.json())

    async def set_facturacion(
        self, external_ref: str, *, iibb: str | None = None, nombre_comercial: str | None = None
    ) -> FacturacionResult:
        payload: dict = {}
        if iibb is not None:
            payload["iibb"] = iibb
        if nombre_comercial is not None:
            payload["nombre_comercial"] = nombre_comercial
        resp = await self._http.put(f"/clientes/{external_ref}/facturacion", json=payload)
        _raise_for_status(resp)
        return FacturacionResult._from_json(resp.json())

    # ------------------------------------------------------------------
    # Onboarding de credencial
    # ------------------------------------------------------------------

    async def generar_csr(
        self, external_ref: str, cuit: str, *, regenerar: bool = False
    ) -> GenerarCsrResult:
        resp = await self._http.post(
            f"/clientes/{external_ref}/csr", json={"cuit": cuit, "regenerar": regenerar}
        )
        _raise_for_status(resp)
        return GenerarCsrResult._from_json(resp.json())

    async def completar_credencial(
        self, external_ref: str, cert_pem: str, *, point_of_sale: int = 0
    ) -> CredencialResult:
        resp = await self._http.post(
            f"/clientes/{external_ref}/credencial/completar",
            json={"cert_pem": cert_pem, "point_of_sale": point_of_sale},
        )
        _raise_for_status(resp)
        return CredencialResult._from_json(resp.json())

    async def importar_credencial(
        self,
        external_ref: str,
        cuit: str,
        cert_pem: str,
        key_pem: str,
        *,
        key_password: str | None = None,
        point_of_sale: int = 0,
    ) -> CredencialResult:
        """`crypto.seal()` (RSA-OAEP + AES-256-GCM sobre una clave privada + password, no
        más de un par de KB) NO se corre en un thread aparte -- es cómputo puro, del
        orden de milisegundos para un payload así de chico, muy lejos del umbral donde
        bloquear el event loop importa de verdad."""
        pub_resp = await self._http.get("/envelope/clave-publica")
        _raise_for_status(pub_resp)
        public_key_pem = pub_resp.json()["public_key_pem"]

        secreto = {"key_pem": key_pem, "key_password": key_password}
        sealed = seal(json.dumps(secreto).encode(), public_key_pem.encode())

        resp = await self._http.post(
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

    async def diagnosticar_credencial(self, external_ref: str) -> DiagnosticoResult:
        resp = await self._http.post(f"/clientes/{external_ref}/credencial/diagnostico")
        _raise_for_status(resp)
        return DiagnosticoResult._from_json(resp.json())

    async def listar_puntos_de_venta(self, external_ref: str) -> PuntosVentaResult:
        resp = await self._http.get(f"/clientes/{external_ref}/credencial/puntos-venta")
        _raise_for_status(resp)
        return PuntosVentaResult._from_json(resp.json())

    # ------------------------------------------------------------------
    # Conexión AFIP embebida (iframe) -- ver ArcaServiceClient.crear_conexion_afip_embed_token
    # (client.py) para el detalle completo, es la misma doc.
    # ------------------------------------------------------------------

    async def crear_conexion_afip_embed_token(
        self, external_ref: str
    ) -> ConexionAfipEmbedTokenResult:
        resp = await self._http.post(f"/clientes/{external_ref}/conexion-afip/embed-token")
        _raise_for_status(resp)
        return ConexionAfipEmbedTokenResult._from_json(resp.json())

    # ------------------------------------------------------------------
    # Padrón
    # ------------------------------------------------------------------

    async def consultar_padron(self, external_ref: str, cuit: str) -> PersonaArca:
        resp = await self._http.get(f"/clientes/{external_ref}/padron/{cuit}")
        _raise_for_status(resp)
        return PersonaArca._from_json(resp.json())

    # ------------------------------------------------------------------
    # Preview — sin efectos secundarios, no pide CAE ni persiste nada.
    # ------------------------------------------------------------------

    async def preview_comprobante(
        self, external_ref: str, comprobante: ComprobanteInput
    ) -> PreviewResult:
        resp = await self._http.post(
            f"/clientes/{external_ref}/comprobantes/preview", json=comprobante.to_payload()
        )
        _raise_for_status(resp)
        return PreviewResult._from_json(resp.json())

    async def preview_nota_credito(
        self, external_ref: str, nota_credito: ComprobanteInput
    ) -> PreviewResult:
        resp = await self._http.post(
            f"/clientes/{external_ref}/notas-credito/preview", json=nota_credito.to_payload()
        )
        _raise_for_status(resp)
        return PreviewResult._from_json(resp.json())

    async def preview_nota_debito(
        self, external_ref: str, nota_debito: ComprobanteInput
    ) -> PreviewResult:
        resp = await self._http.post(
            f"/clientes/{external_ref}/notas-debito/preview", json=nota_debito.to_payload()
        )
        _raise_for_status(resp)
        return PreviewResult._from_json(resp.json())

    # ------------------------------------------------------------------
    # Preview renderizado — el .html/.pdf/.imagen de un preview, antes de
    # emitir. `layout` va en el mismo body que el resto del comprobante.
    # ------------------------------------------------------------------

    async def preview_comprobante_html(
        self, external_ref: str, comprobante: ComprobanteInput, *, layout: str = LAYOUT_DEFAULT
    ) -> str:
        resp = await self._http.post(
            f"/clientes/{external_ref}/comprobantes/preview/comprobante.html",
            json={**comprobante.to_payload(), "layout": layout},
        )
        _raise_for_status(resp)
        return resp.text

    async def preview_comprobante_pdf(
        self, external_ref: str, comprobante: ComprobanteInput, *, layout: str = LAYOUT_DEFAULT
    ) -> bytes:
        resp = await self._http.post(
            f"/clientes/{external_ref}/comprobantes/preview/comprobante.pdf",
            json={**comprobante.to_payload(), "layout": layout},
        )
        _raise_for_status(resp)
        return resp.content

    async def preview_comprobante_imagen(
        self, external_ref: str, comprobante: ComprobanteInput, *, layout: str = LAYOUT_DEFAULT
    ) -> bytes:
        resp = await self._http.post(
            f"/clientes/{external_ref}/comprobantes/preview/comprobante.imagen",
            json={**comprobante.to_payload(), "layout": layout},
        )
        _raise_for_status(resp)
        return resp.content

    async def preview_nota_credito_html(
        self, external_ref: str, nota_credito: ComprobanteInput, *, layout: str = LAYOUT_DEFAULT
    ) -> str:
        resp = await self._http.post(
            f"/clientes/{external_ref}/notas-credito/preview/comprobante.html",
            json={**nota_credito.to_payload(), "layout": layout},
        )
        _raise_for_status(resp)
        return resp.text

    async def preview_nota_credito_pdf(
        self, external_ref: str, nota_credito: ComprobanteInput, *, layout: str = LAYOUT_DEFAULT
    ) -> bytes:
        resp = await self._http.post(
            f"/clientes/{external_ref}/notas-credito/preview/comprobante.pdf",
            json={**nota_credito.to_payload(), "layout": layout},
        )
        _raise_for_status(resp)
        return resp.content

    async def preview_nota_credito_imagen(
        self, external_ref: str, nota_credito: ComprobanteInput, *, layout: str = LAYOUT_DEFAULT
    ) -> bytes:
        resp = await self._http.post(
            f"/clientes/{external_ref}/notas-credito/preview/comprobante.imagen",
            json={**nota_credito.to_payload(), "layout": layout},
        )
        _raise_for_status(resp)
        return resp.content

    async def preview_nota_debito_html(
        self, external_ref: str, nota_debito: ComprobanteInput, *, layout: str = LAYOUT_DEFAULT
    ) -> str:
        resp = await self._http.post(
            f"/clientes/{external_ref}/notas-debito/preview/comprobante.html",
            json={**nota_debito.to_payload(), "layout": layout},
        )
        _raise_for_status(resp)
        return resp.text

    async def preview_nota_debito_pdf(
        self, external_ref: str, nota_debito: ComprobanteInput, *, layout: str = LAYOUT_DEFAULT
    ) -> bytes:
        resp = await self._http.post(
            f"/clientes/{external_ref}/notas-debito/preview/comprobante.pdf",
            json={**nota_debito.to_payload(), "layout": layout},
        )
        _raise_for_status(resp)
        return resp.content

    async def preview_nota_debito_imagen(
        self, external_ref: str, nota_debito: ComprobanteInput, *, layout: str = LAYOUT_DEFAULT
    ) -> bytes:
        resp = await self._http.post(
            f"/clientes/{external_ref}/notas-debito/preview/comprobante.imagen",
            json={**nota_debito.to_payload(), "layout": layout},
        )
        _raise_for_status(resp)
        return resp.content

    # ------------------------------------------------------------------
    # Emisión — asincrónica del lado de arca-service (responde `pending` de
    # inmediato, el resultado real llega por polling/webhook) -- no
    # confundir con que el MÉTODO acá sea `async def`, son dos cosas
    # distintas que coinciden de casualidad en el nombre.
    # ------------------------------------------------------------------

    async def emitir_comprobante(
        self, external_ref: str, comprobante: ComprobanteInput
    ) -> EmisionResult:
        resp = await self._http.post(
            f"/clientes/{external_ref}/comprobantes", json=comprobante.to_payload()
        )
        _raise_for_status(resp)
        return EmisionResult._from_json(resp.json())

    async def emitir_nota_credito(
        self, external_ref: str, nota_credito: ComprobanteInput
    ) -> EmisionResult:
        resp = await self._http.post(
            f"/clientes/{external_ref}/notas-credito", json=nota_credito.to_payload()
        )
        _raise_for_status(resp)
        return EmisionResult._from_json(resp.json())

    async def emitir_nota_debito(
        self, external_ref: str, nota_debito: ComprobanteInput
    ) -> EmisionResult:
        resp = await self._http.post(
            f"/clientes/{external_ref}/notas-debito", json=nota_debito.to_payload()
        )
        _raise_for_status(resp)
        return EmisionResult._from_json(resp.json())

    async def get_comprobante(self, external_ref: str, idempotency_key: str) -> EmisionResult:
        resp = await self._http.get(f"/clientes/{external_ref}/comprobantes/{idempotency_key}")
        _raise_for_status(resp)
        return EmisionResult._from_json(resp.json())

    async def listar_comprobantes(
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
        resp = await self._http.get(f"/clientes/{external_ref}/comprobantes", params=params)
        _raise_for_status(resp)
        return ListaComprobantesResult._from_json(resp.json())

    # ------------------------------------------------------------------
    # Sesión embebida (iframe) -- ver
    # ArcaServiceClient.crear_sesion_embebida_comprobante (client.py) para
    # el detalle completo, es la misma doc.
    # ------------------------------------------------------------------

    async def crear_sesion_embebida_comprobante(
        self, external_ref: str, comprobante: SesionEmbebidaInput
    ) -> SesionEmbebidaResult:
        resp = await self._http.post(
            f"/clientes/{external_ref}/comprobantes/sesion-embebida",
            json=comprobante.to_payload(),
        )
        _raise_for_status(resp)
        return SesionEmbebidaResult._from_json(resp.json())

    async def crear_sesion_embebida_nota_credito(
        self, external_ref: str, nota_credito: SesionEmbebidaInput
    ) -> SesionEmbebidaResult:
        resp = await self._http.post(
            f"/clientes/{external_ref}/notas-credito/sesion-embebida",
            json=nota_credito.to_payload(),
        )
        _raise_for_status(resp)
        return SesionEmbebidaResult._from_json(resp.json())

    async def crear_sesion_embebida_nota_debito(
        self, external_ref: str, nota_debito: SesionEmbebidaInput
    ) -> SesionEmbebidaResult:
        resp = await self._http.post(
            f"/clientes/{external_ref}/notas-debito/sesion-embebida",
            json=nota_debito.to_payload(),
        )
        _raise_for_status(resp)
        return SesionEmbebidaResult._from_json(resp.json())

    # ------------------------------------------------------------------
    # Lote — ver `ArcaServiceClient.emitir_lote_comprobantes` para el
    # detalle del fallo parcial por ítem.
    # ------------------------------------------------------------------

    async def emitir_lote_comprobantes(
        self, external_ref: str, comprobantes: list[ComprobanteInput]
    ) -> list[LoteItemResult]:
        resp = await self._http.post(
            f"/clientes/{external_ref}/comprobantes/lote",
            json={"comprobantes": [c.to_payload() for c in comprobantes]},
        )
        _raise_for_status(resp)
        return [LoteItemResult._from_json(item) for item in resp.json()]

    async def emitir_lote_notas_credito(
        self, external_ref: str, notas_credito: list[ComprobanteInput]
    ) -> list[LoteItemResult]:
        resp = await self._http.post(
            f"/clientes/{external_ref}/notas-credito/lote",
            json={"notas_credito": [n.to_payload() for n in notas_credito]},
        )
        _raise_for_status(resp)
        return [LoteItemResult._from_json(item) for item in resp.json()]

    async def emitir_lote_notas_debito(
        self, external_ref: str, notas_debito: list[ComprobanteInput]
    ) -> list[LoteItemResult]:
        resp = await self._http.post(
            f"/clientes/{external_ref}/notas-debito/lote",
            json={"notas_debito": [n.to_payload() for n in notas_debito]},
        )
        _raise_for_status(resp)
        return [LoteItemResult._from_json(item) for item in resp.json()]

    # ------------------------------------------------------------------
    # Webhooks
    # ------------------------------------------------------------------

    async def reenviar_webhook(self, external_ref: str, idempotency_key: str) -> EmisionResult:
        resp = await self._http.post(
            f"/clientes/{external_ref}/comprobantes/{idempotency_key}/webhook/reenviar"
        )
        _raise_for_status(resp)
        return EmisionResult._from_json(resp.json())

    # ------------------------------------------------------------------
    # Documento renderizado
    # ------------------------------------------------------------------

    async def get_comprobante_html(
        self, external_ref: str, idempotency_key: str, *, layout: str = LAYOUT_DEFAULT
    ) -> str:
        resp = await self._http.get(
            f"/clientes/{external_ref}/comprobantes/{idempotency_key}/comprobante.html",
            params={"layout": layout},
        )
        _raise_for_status(resp)
        return resp.text

    async def get_comprobante_pdf(
        self, external_ref: str, idempotency_key: str, *, layout: str = LAYOUT_DEFAULT
    ) -> bytes:
        resp = await self._http.get(
            f"/clientes/{external_ref}/comprobantes/{idempotency_key}/comprobante.pdf",
            params={"layout": layout},
        )
        _raise_for_status(resp)
        return resp.content

    async def get_comprobante_imagen(
        self, external_ref: str, idempotency_key: str, *, layout: str = LAYOUT_DEFAULT
    ) -> bytes:
        resp = await self._http.get(
            f"/clientes/{external_ref}/comprobantes/{idempotency_key}/comprobante.imagen",
            params={"layout": layout},
        )
        _raise_for_status(resp)
        return resp.content

    # ------------------------------------------------------------------
    # Vista embebible (iframe) -- ver ArcaServiceClient.crear_embed_token
    # (client.py) para el detalle completo, es la misma doc.
    # ------------------------------------------------------------------

    async def crear_embed_token(self, external_ref: str, idempotency_key: str) -> EmbedTokenResult:
        resp = await self._http.post(
            f"/clientes/{external_ref}/comprobantes/{idempotency_key}/embed-token"
        )
        _raise_for_status(resp)
        return EmbedTokenResult._from_json(resp.json())
