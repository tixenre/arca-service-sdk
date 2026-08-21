"""arca_service_client — cliente HTTP oficial para arca-service (tixenre/arca-service).

    from arca_service_client import ArcaServiceClient, ComprobanteInput, CondicionIva, DocTipo

    client = ArcaServiceClient(
        base_url="https://arca.tudominio.com",
        client_cert_path="/etc/ganche/arca-client.crt",
        client_key_path="/etc/ganche/arca-client.key",
        api_key="...",
    )
    emision = client.emitir_comprobante(
        "org-123",
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

Sin dependencia de Django/Celery/saas-core/arca_fe a propósito — solo `httpx` (mTLS
nativo vía `cert=(cert, key)`) y `cryptography` (`crypto.seal`, para
`importar_credencial`). Ver README.md para la guía completa (onboarding, idempotencia,
webhooks)."""

from __future__ import annotations

from .client import ArcaServiceClient
from .crypto import EnvelopeError, seal
from .enums import Alicuota, CbteTipo, Concepto, CondicionIva, DocTipo
from .exceptions import (
    AfipUnavailableError,
    ArcaServiceError,
    ArcaServiceServerError,
    IdempotencyConflictError,
    NotFoundError,
    RateLimitedError,
    ServiceNotReadyError,
    ValidationError,
)
from .models import (
    Actividad,
    Caracterizacion,
    Categoria,
    Chequeo,
    ComponenteSociedad,
    ComprobanteAsociado,
    ComprobanteInput,
    CredencialResult,
    Dependencia,
    DiagnosticoResult,
    Domicilio,
    EmisionResult,
    GenerarCsrResult,
    Impuesto,
    ItemFactura,
    ItemIva,
    Opcional,
    PersonaArca,
    PreviewResult,
    PuntosVentaResult,
    PuntoVentaExcluido,
    PuntoVentaHabilitado,
    Regimen,
    Tributo,
)
from .webhooks import verify_webhook_signature

__version__ = "0.0.1"

__all__ = [
    "__version__",
    "ArcaServiceClient",
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
    "Tributo",
    # response
    "Actividad",
    "Caracterizacion",
    "Categoria",
    "Chequeo",
    "ComponenteSociedad",
    "CredencialResult",
    "Dependencia",
    "DiagnosticoResult",
    "Domicilio",
    "EmisionResult",
    "GenerarCsrResult",
    "Impuesto",
    "PersonaArca",
    "PreviewResult",
    "PuntosVentaResult",
    "PuntoVentaExcluido",
    "PuntoVentaHabilitado",
    "Regimen",
    # envelope (importar_credencial)
    "EnvelopeError",
    "seal",
    # webhooks
    "verify_webhook_signature",
    # errores
    "ArcaServiceError",
    "AfipUnavailableError",
    "ArcaServiceServerError",
    "IdempotencyConflictError",
    "NotFoundError",
    "RateLimitedError",
    "ServiceNotReadyError",
    "ValidationError",
]
