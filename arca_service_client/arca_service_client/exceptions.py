"""arca_service_client.exceptions — un tipo por `type` del sobre de error de
arca-service, más un puñado de subclases con nombre propio para los `code` que vale la
pena distinguir sin obligar a mirar `.code` a mano (los conflictos de negocio que ya
existían antes de este sobre, y los tres `code` nuevos que trae la migración). El sobre
real:

    {"error": {"type": "afip", "code": "afip_rechazo", "message": "...", "afip": [...]}}

`type` son CUATRO y no crecen (`request`/`configuracion`/`afip`/`interno` — ver cada
subclase de más abajo para qué significa cada uno). `code` es el motivo puntual y SÍ
crece con el tiempo, así que un `code` que este módulo no reconoce cae en la subclase de
su `type`, nunca en un catch-all sin tipar — no hace falta enumerar cada `code` acá para
que un error nuevo del servidor siga siendo manejable.

`ArcaServiceError` es la base común: quien solo necesita `except ArcaServiceError` para
"algo salió mal" no tiene que conocer cada subtipo; quien sí necesita discriminar puede
mirar `.code` (estable, para programas) o `.type` (grueso, para decidir qué hacer) — nunca
`.message`, que está escrito para que lo lea una persona, no para que un programa lo
compare."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import httpx


@dataclass(frozen=True)
class AfipErrorDetail:
    """Un ítem de `AfipRechazoError.afip` — un código de rechazo de AFIP tal cual, sin
    masticar (`codigo` es el numérico de WSFEv1, ej. `10016`; `mensaje` es el texto que
    manda AFIP para ese código, no reescrito por arca-service)."""

    codigo: int
    mensaje: str


class ArcaServiceError(Exception):
    """Base de todo error que arca-service devolvió como respuesta HTTP (status >= 400).
    NO cubre fallas de transporte (timeout, DNS, conexión rechazada) — esas las levanta
    `httpx` directo (`httpx.TimeoutException`, `httpx.ConnectError`, etc.); envolverlas
    acá mezclaría "arca-service respondió que no" con "ni siquiera pudimos preguntarle",
    dos causas con remedios distintos.

    `param`: el campo del request al que apunta el error, cuando aplica (`None` si no).
    `afip`: los códigos de rechazo de AFIP sin masticar, solo poblado en
    `AfipRechazoError` (`None` en cualquier otra subclase)."""

    def __init__(
        self,
        *,
        type: str,
        code: str,
        message: str,
        status_code: int,
        param: str | None = None,
        afip: tuple[AfipErrorDetail, ...] | None = None,
        response: httpx.Response | None = None,
    ):
        super().__init__(message)
        self.type = type
        self.code = code
        self.message = message
        self.param = param
        self.afip = afip
        self.status_code = status_code
        self.response = response


class RequestError(ArcaServiceError):
    """`type: "request"` — el problema está en lo que mandaste. Cambiá el request (según
    `.code`/`.param`) y reintentá; reintentar sin cambiar nada da el mismo resultado."""


class ConfiguracionError(ArcaServiceError):
    """`type: "configuracion"` — nada que este código pueda arreglar: el dueño del CUIT
    tiene un trámite pendiente del lado de AFIP (portal de AFIP), no un problema de este
    request puntual. Reintentar el mismo request no cambia nada hasta que se resuelva
    afuera."""


class AfipError(ArcaServiceError):
    """`type: "afip"` — la respuesta vino de AFIP, no de arca-service. `.code` dice si
    reintentar sirve: un rechazo (`AfipRechazoError`) no se resuelve solo; que AFIP no
    haya contestado (`AfipUnavailableError`) sí puede resolverse reintentando más
    tarde."""


class InternoError(ArcaServiceError):
    """`type: "interno"` — problema del lado de arca-service o de su infraestructura, no
    tuyo ni del Cliente. Avisale a arca-service si persiste."""


class NotFoundError(RequestError):
    """404 — el recurso pedido (comprobante, credencial) no existe para este Cliente, o el
    `external_ref` mismo no existe / tu Plataforma no está autorizada contra él --
    deliberadamente el mismo 404 genérico para los dos casos, para no filtrarle a un
    caller no autorizado si un `external_ref` existe o no."""


class IdempotencyConflictError(RequestError):
    """409 — ya existe un intento con esa `idempotency_key` pero con datos DISTINTOS.
    Elegí una key nueva si es una emisión genuinamente distinta; si es un reintento de la
    MISMA emisión con el MISMO payload, esto no debería pasar -- construila
    determinística a partir de algo estable de tu propio dominio (ej. el id de la orden
    o factura en tu sistema), nunca de un valor random generado en cada intento."""


class BonificadoLimiteError(ConfiguracionError):
    """409 — `ArcaServiceClient.set_bonificado(external_ref, True)` chocó contra el
    circuit-breaker de seguridad de tu Plataforma (0 por default -- fail-closed hasta
    que un operador de arca-service negocie un límite real y lo suba a mano). NO es un
    error tuyo ni del Cliente: pedile a arca-service que revise/suba el límite. Desactivar
    (`set_bonificado(external_ref, False)`) nunca choca contra esto -- solo activar un
    vínculo nuevo cuenta contra el límite."""


class CsrYaExisteError(RequestError):
    """409 — `ArcaServiceClient.generar_csr(external_ref)` chocó con un CSR que ya se
    había generado antes para este Cliente y todavía no se completó con un certificado
    (`completar_credencial`). Pasá `regenerar=True` si el objetivo es descartar ese CSR
    pendiente y arrancar de cero con uno nuevo."""


class CredencialYaActivaError(RequestError):
    """409 — `ArcaServiceClient.generar_csr(external_ref)` chocó con una credencial que
    ya está activa para este Cliente. Pasá `regenerar=True` si el objetivo es
    reemplazarla por una nueva (la activa deja de servir en cuanto se complete la
    nueva)."""


class RateLimitedError(RequestError):
    """429 — se excedió el límite de requests por Plataforma. `retry_after`: segundos a
    esperar antes de reintentar (header `Retry-After`, ya redondeado hacia arriba por el
    servidor) — `None` si el servidor no lo mandó."""

    def __init__(self, *, retry_after: int | None = None, **kwargs: object):
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self.retry_after = retry_after


class PuntoVentaNoHabilitadoError(ConfiguracionError):
    """422 — el punto de venta desde el que se iba a emitir no está habilitado en AFIP:
    bloqueado, dado de baja, o no electrónico. Se arregla en el portal de AFIP, nunca
    cambiando el request -- arca-service lo verifica ANTES de pedirle el CAE a AFIP, así
    que esto llega sin haber tocado la red de AFIP para nada."""


class NotaExcedeComprobanteError(RequestError):
    """422, `param == "comprobante_asociado"` — la nota de crédito/débito acredita más de
    lo que queda disponible en la factura que referencia. `.message` dice cuánto queda;
    mandá un importe que entre en eso. `request` y no `afip`: esto lo rechaza
    arca-service, AFIP nunca lo vio."""


class AfipRechazoError(AfipError):
    """422 — AFIP rechazó el comprobante. NO reintentable sin cambiar algo: es un
    rechazo, no un timeout. `.afip`: los códigos de rechazo tal cual los mandó AFIP
    (`AfipErrorDetail.codigo`/`.mensaje`) -- `.message` ya los incluye concatenados para
    mostrar a una persona; `.afip` es para programar contra el código numérico (ej.
    distinguir el 10016 de fecha fuera de rango de otro rechazo)."""


class AfipUnavailableError(AfipError):
    """502 — AFIP no contestó (timeout de WSAA/WSFEv1) o contestó en un formato que
    arca-service no pudo interpretar (SOAP Fault). A diferencia de `AfipRechazoError`,
    esto SÍ es transitorio -- reintentar más tarde (con backoff, no en loop) puede
    andar."""


class ServicioNoDisponibleError(InternoError):
    """503 — arca-service no puede completar este request puntual, aunque el servicio en
    general esté arriba (ver `GET /readyz`). Hoy lo devuelven `get_comprobante_html`/
    `_pdf`/`_imagen` cuando el renderizador no está disponible -- **no significa que la
    emisión haya fallado**: el CAE ya existe, `get_comprobante` lo sigue mostrando, y el
    HTML/PDF/imagen se puede volver a pedir cuando el renderizador vuelva. Reintentable."""
