"""arca_service_client — cliente HTTP oficial para arca-service (tixenre/arca-service).

    from arca_service_client import ArcaServiceClient, ComprobanteInput, CondicionIva, DocTipo

    client = ArcaServiceClient(
        base_url="https://arca.tudominio.com",
        client_cert_path="/etc/mi-plataforma/arca-client.crt",
        client_key_path="/etc/mi-plataforma/arca-client.key",
        api_key="...",
    )

    # Primer llamado siempre: resuelve (o crea) el Cliente dueño de este CUIT, y
    # crea/reactiva el vínculo de TU Plataforma con él.
    onboarding = client.por_cuit("20301234563")

    emision = client.emitir_comprobante(
        onboarding.external_ref,
        ComprobanteInput(
            idempotency_key="factura-8231",
            concepto=Concepto.PRODUCTOS,
            emisor_condicion_iva=CondicionIva.RESPONSABLE_INSCRIPTO,
            receptor_doc_tipo=DocTipo.DNI,
            receptor_doc_nro="12345678",
            receptor_condicion_iva=CondicionIva.CONSUMIDOR_FINAL,
            fecha=date.today(),
            importe_neto=Decimal("1000.00"),
            alicuota_unica=Alicuota.IVA_21,
        ),
    )

Sin dependencias pesadas a propósito — solo `httpx` (mTLS nativo vía `cert=(cert,
key)`) y `cryptography` (`crypto.seal`, para `importar_credencial`). Ver README.md para
la guía completa (onboarding, idempotencia, webhooks).

¿Consumidor async (FastAPI)? `AsyncArcaServiceClient` (`async_client.py`) es la misma
API con `async def`/`await` en cada método, sobre `httpx.AsyncClient`."""

from __future__ import annotations

from .async_client import AsyncArcaServiceClient
from .client import ArcaServiceClient
from .crypto import EnvelopeError, seal
from .enums import Alicuota, CbteTipo, Concepto, CondicionIva, DocTipo
from .exceptions import (
    AfipError,
    AfipErrorDetail,
    AfipRechazoError,
    AfipUnavailableError,
    ArcaServiceError,
    BonificadoLimiteError,
    ConfiguracionError,
    IdempotencyConflictError,
    InternoError,
    NotaExcedeComprobanteError,
    NotFoundError,
    PuntoVentaNoHabilitadoError,
    RateLimitedError,
    RequestError,
    ServicioNoDisponibleError,
)
from .local_config import CredentialsNotFoundError
from .models import (
    Actividad,
    BonificadoResult,
    Caracterizacion,
    Categoria,
    Chequeo,
    ComponenteSociedad,
    ComprobanteAsociado,
    ComprobanteInput,
    ConexionAfipEmbedTokenResult,
    CredencialResult,
    Dependencia,
    DiagnosticoResult,
    Domicilio,
    EmbedTokenResult,
    EmisionResult,
    GenerarCsrResult,
    Impuesto,
    ItemFactura,
    ItemIva,
    LoteItemResult,
    OnboardingResult,
    Opcional,
    PersonaArca,
    PreviewResult,
    PuntosVentaResult,
    PuntoVentaExcluido,
    PuntoVentaHabilitado,
    Regimen,
    SesionEmbebidaInput,
    SesionEmbebidaResult,
    Tributo,
)
from .webhooks import verify_webhook_signature

__version__ = "0.0.6"

__all__ = [
    "__version__",
    "ArcaServiceClient",
    "AsyncArcaServiceClient",
    # enums (códigos AFIP — ver enums.py)
    "Alicuota",
    "CbteTipo",
    "Concepto",
    "CondicionIva",
    "DocTipo",
    # request
    "ComprobanteAsociado",
    "ComprobanteInput",
    "ItemFactura",
    "ItemIva",
    "Opcional",
    "SesionEmbebidaInput",
    "Tributo",
    # response
    "Actividad",
    "BonificadoResult",
    "Caracterizacion",
    "Categoria",
    "Chequeo",
    "ComponenteSociedad",
    "ConexionAfipEmbedTokenResult",
    "CredencialResult",
    "Dependencia",
    "DiagnosticoResult",
    "Domicilio",
    "EmbedTokenResult",
    "EmisionResult",
    "GenerarCsrResult",
    "Impuesto",
    "LoteItemResult",
    "OnboardingResult",
    "PersonaArca",
    "PreviewResult",
    "PuntosVentaResult",
    "PuntoVentaExcluido",
    "PuntoVentaHabilitado",
    "Regimen",
    "SesionEmbebidaResult",
    # envelope (importar_credencial)
    "EnvelopeError",
    "seal",
    # webhooks
    "verify_webhook_signature",
    # errores -- ver exceptions.py para el sobre {"error": {type, code, message, ...}}
    # y qué type/code cae en cada excepción.
    "ArcaServiceError",
    "AfipError",
    "AfipErrorDetail",
    "AfipRechazoError",
    "AfipUnavailableError",
    "BonificadoLimiteError",
    "ConfiguracionError",
    "CredentialsNotFoundError",
    "IdempotencyConflictError",
    "InternoError",
    "NotaExcedeComprobanteError",
    "NotFoundError",
    "PuntoVentaNoHabilitadoError",
    "RateLimitedError",
    "RequestError",
    "ServicioNoDisponibleError",
]
