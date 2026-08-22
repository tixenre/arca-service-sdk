"""arca_service_client.exceptions — un tipo por código de status HTTP (y, para el 409,
también por SIGNIFICADO DE NEGOCIO — ver `BonificadoLimiteError`) que arca-service puede
devolver. Todo error sale como `{"detail": "..."}`, siempre string plano — nunca una
lista de objetos ni un shape distinto según el error. `ArcaServiceError` es la base
común: quien solo necesita `except ArcaServiceError` para "algo salió mal" no tiene que
conocer cada subtipo; quien sí necesita discriminar (reintentar un 429/502, no
reintentar un 422) puede."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import httpx


class ArcaServiceError(Exception):
    """Base de todo error que arca-service devolvió como respuesta HTTP (status >= 400).
    NO cubre fallas de transporte (timeout, DNS, conexión rechazada) — esas las levanta
    `httpx` directo (`httpx.TimeoutException`, `httpx.ConnectError`, etc.); envolverlas
    acá mezclaría "arca-service respondió que no" con "ni siquiera pudimos preguntarle",
    dos causas con remedios distintos."""

    def __init__(self, detail: str, *, status_code: int, response: httpx.Response | None = None):
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code
        self.response = response


class NotFoundError(ArcaServiceError):
    """404 — el recurso pedido (comprobante, credencial) no existe para este Cliente, o el
    `external_ref` mismo no existe / tu Plataforma no está autorizada contra él --
    deliberadamente el mismo 404 genérico para los dos casos, para no filtrarle a un
    caller no autorizado si un `external_ref` existe o no."""


class IdempotencyConflictError(ArcaServiceError):
    """409 — ya existe un intento con esa `idempotency_key` pero con datos DISTINTOS.
    Elegí una key nueva si es una emisión genuinamente distinta; si es un reintento de la
    MISMA emisión con el MISMO payload, esto no debería pasar -- construila
    determinística a partir de algo estable de tu propio dominio (ej. el id de la orden
    o factura en tu sistema), nunca de un valor random generado en cada intento.

    NO es el tipo que levanta `set_bonificado` en su propio 409 — ver
    `BonificadoLimiteError`, un conflicto de negocio totalmente distinto que
    comparte el status code pero no el significado."""


class ValidationError(ArcaServiceError):
    """422 — el request es válido a nivel HTTP/JSON pero rechazado por una regla de
    negocio (campo inconsistente, o AFIP mismo rechazó por una regla suya — ej. CUIT sin
    padrón). Reintentar sin cambiar nada da el mismo resultado."""


class RateLimitedError(ArcaServiceError):
    """429 — se excedió el límite de requests por Plataforma. `retry_after`: segundos a
    esperar antes de reintentar (header `Retry-After`, ya redondeado hacia arriba por el
    servidor) — `None` si el servidor no lo mandó."""

    def __init__(
        self,
        detail: str,
        *,
        status_code: int,
        response: httpx.Response | None = None,
        retry_after: int | None = None,
    ):
        super().__init__(detail, status_code=status_code, response=response)
        self.retry_after = retry_after


class AfipUnavailableError(ArcaServiceError):
    """502 — arca-service recibió el request bien, pero AFIP no respondió o respondió en
    un formato inesperado (timeout de WSAA/WSFEv1, SOAP Fault). Falla TRANSITORIA del
    lado de AFIP — reintentar más tarde puede andar (con backoff, no en loop)."""


class ServiceNotReadyError(ArcaServiceError):
    """503 — arca-service todavía no tiene algo que necesita para responder (ej. el par
    de claves de envelope no está configurado, ver `GET /envelope/clave-publica`) — no es
    un problema del request, es el servicio mismo sin terminar de arrancar."""


class ArcaServiceServerError(ArcaServiceError):
    """500, o cualquier status >= 400 sin un tipo más específico acá arriba — bug del
    lado de arca-service. `detail` va a ser genérico ("Error interno del servicio.") a
    propósito: el servidor nunca expone detalle interno en un 500 real."""


class BonificadoLimiteError(ArcaServiceError):
    """409 — `ArcaServiceClient.set_bonificado(external_ref, True)` chocó contra el
    circuit-breaker de seguridad de tu Plataforma (0 por default -- fail-closed hasta
    que un operador de arca-service negocie un límite real y lo suba a mano). NO es un
    error tuyo ni del Cliente: pedile a arca-service que revise/suba el límite. Desactivar
    (`set_bonificado(external_ref, False)`) nunca choca contra esto -- solo activar un
    vínculo nuevo cuenta contra el límite."""
