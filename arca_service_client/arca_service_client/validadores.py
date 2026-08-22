"""arca_service_client.validadores — normalizar/validar/formatear CUIT. PORTABLE (solo stdlib).

Mismo algoritmo mod-11 que `arca_fe.validadores` — duplicado acá, NO importado: este
paquete es un cliente HTTP delgado (solo `httpx`/`cryptography`) a propósito, para que un
consumidor pueda instalarlo sin arrastrar las dependencias de `arca_fe` (zeep, pyhanko,
Playwright, etc.) solo para validar un string. Mismo motivo por el que `arca_fe` tampoco
importa desde ningún otro lado para esto — es aritmética pura, sin estado, barata de
mantener sincronizada a mano en el puñado de lugares que la necesitan.

Pensado para validación LOCAL, sin red — antes de mandarle nada a arca-service (ej.
`client.por_cuit(cuit)`), o para feedback instantáneo en un formulario propio. arca-service
igual revalida esto mismo del lado servidor (nunca confía en que el caller ya validó) — este
módulo es una comodidad para el consumidor, no un reemplazo de esa validación.

Criterio de ingesta (aplica en todo este paquete, no solo acá): normalizar sin preguntar lo
cosmético/no-ambiguo (guiones, espacios), rechazar con motivo explícito lo que es realmente
inválido (dígito verificador mal, largo incorrecto). Nunca "adivinar" un dato mal formado.
"""

from __future__ import annotations

_PESOS = (5, 4, 3, 2, 7, 6, 5, 4, 3, 2)


def normalizar_cuit(raw: str | int | None) -> str | None:
    """Deja solo los dígitos de `raw` (tolera guiones, espacios, cualquier separador).

    Devuelve el string de 11 dígitos, o `None` si no quedan exactamente 11 (ni error ni
    excepción — es una normalización best-effort; el llamador decide qué hacer con `None`)."""
    if raw is None or raw == "":
        return None
    digitos = "".join(c for c in str(raw) if c.isdigit())
    return digitos if len(digitos) == 11 else None


def cuit_valido(cuit: str | int | None) -> bool:
    """Valida el dígito verificador (mod-11) de un CUIT/CUIL de 11 dígitos.

    Normaliza primero (tolera guiones/espacios) — un CUIT con guiones y uno sin guiones dan
    exactamente el mismo resultado."""
    n = normalizar_cuit(cuit)
    if n is None:
        return False
    suma = sum(int(d) * p for d, p in zip(n[:10], _PESOS))
    resto = 11 - (suma % 11)
    verificador = 0 if resto == 11 else (9 if resto == 10 else resto)
    return verificador == int(n[10])


def formatear_cuit(cuit: str | int) -> str:
    """Devuelve el CUIT formateado para MOSTRAR: `XX-XXXXXXXX-X` (el estándar de AFIP).

    Nunca para guardar — normaliza (tolera guiones/espacios en la entrada) y arma el formato
    con guiones. `ValueError` si no normaliza a 11 dígitos (no se intenta rellenar/truncar —
    eso sería adivinar un dato mal formado)."""
    n = normalizar_cuit(cuit)
    if n is None:
        raise ValueError(f"No se pudo normalizar '{cuit}' a un CUIT de 11 dígitos para formatear.")
    return f"{n[:2]}-{n[2:10]}-{n[10]}"
