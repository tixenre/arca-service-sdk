"""arca_service_client.models — dataclasses de request/response, espejo exacto del
contrato JSON real de la API. Los de REQUEST (`ComprobanteInput` y sus componentes)
saben serializarse a sí mismos (`to_payload()`) al shape JSON que la API espera —
Decimal -> string (nunca un `float` crudo: evita sorpresas de representación binaria en
un monto), `date` -> ISO. Los de RESPONSE (`EmisionResult`, `PersonaArca`, etc.) tienen
un `_from_json` que parsea el dict que devuelve `response.json()` — nunca levantan por
un campo extra que la API agregue después (`dict.get`), sí levantan `KeyError` por uno
FALTANTE que el schema real marca como obligatorio (fail loud, no fabricar un default
para un campo que se supone que siempre viene)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from .exceptions import AfipErrorDetail


def _dec(v: Any) -> Decimal:
    return v if isinstance(v, Decimal) else Decimal(str(v))


def _fecha(v: Any) -> date | None:
    return date.fromisoformat(v) if v else None


def _parse_fecha_hora(v: str) -> datetime:
    # `.replace("Z", "+00:00")` -- `datetime.fromisoformat` recién entiende el
    # sufijo "Z" (UTC) nativo desde Python 3.11; este paquete declara
    # `requires-python = ">=3.9"`, así que sin esto un `expires_at` de
    # `crear_embed_token` (el servidor codifica un datetime UTC con "Z", no
    # "+00:00") rompería para cualquier consumidor en 3.9/3.10. No-op si `v`
    # no trae "Z" (offset explícito, o un naive datetime de AFIP).
    return datetime.fromisoformat(v.replace("Z", "+00:00"))


def _fecha_hora(v: Any) -> datetime | None:
    return _parse_fecha_hora(v) if v else None


# ---------------------------------------------------------------------------
# Request — comprobante a emitir/previsualizar
# ---------------------------------------------------------------------------


@dataclass
class Tributo:
    """Un tributo/percepción (Impuestos Internos, percepciones de IIBB, etc.).
    `codigo`: código de la tabla AFIP `FEParamGetTiposTributos`."""

    codigo: int
    base_imponible: Decimal
    alicuota_pct: Decimal
    importe: Decimal
    descripcion: str = ""

    def _to_dict(self) -> dict:
        return {
            "codigo": self.codigo,
            "base_imponible": str(self.base_imponible),
            "alicuota_pct": str(self.alicuota_pct),
            "importe": str(self.importe),
            "descripcion": self.descripcion,
        }


@dataclass
class Opcional:
    """Un dato opcional del comprobante (ej. CBU/Alias de una FCE MiPyme).
    `codigo`: código de la tabla AFIP `FEParamGetTiposOpcional`."""

    codigo: str
    valor: str

    def _to_dict(self) -> dict:
        return {"codigo": self.codigo, "valor": self.valor}


@dataclass
class ItemFactura:
    """Un renglón: qué se vendió, a cuánto, y cómo lo trata el IVA -- la ÚNICA
    fuente de los importes del comprobante, no hay un importe neto aparte para
    reconciliar contra esto.

    Va `precio_unitario` (sin IVA) o `precio_final` (con IVA incluido), nunca los
    dos: no son equivalentes bajo redondeo, así que ninguno se puede derivar del
    otro.

    `iva` es el porcentaje en string (`"21"`, `"10.5"`, `"0"`), nunca un id de
    alícuota -- o `"exento"`/`"no_gravado"` para los dos casos que AFIP trata
    distinto de un 0% discriminado."""

    descripcion: str
    iva: str
    precio_unitario: Decimal | None = None
    precio_final: Decimal | None = None
    codigo: str = ""
    cantidad: Decimal = Decimal("1")
    unidad_medida: str = "unidad"
    bonificacion_pct: Decimal = Decimal("0")
    detalle: str = ""

    def _to_dict(self) -> dict:
        d: dict = {
            "descripcion": self.descripcion,
            "iva": self.iva,
            "codigo": self.codigo,
            "cantidad": str(self.cantidad),
            "unidad_medida": self.unidad_medida,
            "bonificacion_pct": str(self.bonificacion_pct),
            "detalle": self.detalle,
        }
        if self.precio_unitario is not None:
            d["precio_unitario"] = str(self.precio_unitario)
        if self.precio_final is not None:
            d["precio_final"] = str(self.precio_final)
        return d


@dataclass
class ComprobanteAsociado:
    """Referencia a la factura original — obligatoria en una nota de crédito/débito
    (`ComprobanteInput.comprobante_asociado`). Con `tipo`/`punto_venta`/`numero`
    alcanza si el comprobante original lo emitió este mismo servicio -- se busca
    solo, sin pedir más datos.

    `cae`/`importe_total`: para asociar una nota a un comprobante que NO emitió
    este servicio (de antes de migrar, o de otro proveedor) -- van los dos juntos
    o ninguno, el servidor los exige así."""

    tipo: int
    punto_venta: int
    numero: int
    cuit: str | None = None
    fecha: date | None = None
    cae: str | None = None
    importe_total: Decimal | None = None

    def _to_dict(self) -> dict:
        d: dict = {"tipo": int(self.tipo), "punto_venta": self.punto_venta, "numero": self.numero}
        if self.cuit is not None:
            d["cuit"] = self.cuit
        if self.fecha is not None:
            d["fecha"] = self.fecha.isoformat()
        if self.cae is not None:
            d["cae"] = self.cae
        if self.importe_total is not None:
            d["importe_total"] = str(self.importe_total)
        return d


@dataclass
class Receptor:
    """A quién se le factura. Exactamente una de estas tres formas lo identifica
    -- el servidor rechaza si no viene ninguna, o si viene más de una:

        Receptor(cuit="30712345671")
        Receptor(dni="20111222", nombre="Juan Pérez")
        Receptor(consumidor_final=True)

    Con `cuit`, `nombre`/`domicilio` no hace falta mandarlos (y es un 422 si se
    mandan): los resuelve solo el padrón de AFIP. Con `dni` sí se aceptan -- no
    hay padrón que consultar para alguien sin CUIT.

    `condicion_iva`: código de AFIP, salida de emergencia para el único caso que
    el padrón no puede resolver solo -- si el padrón sabe, gana el padrón y esto
    se ignora.

    `email` es aparte y va con cualquiera de las tres formas: no es un dato
    fiscal, es el contacto para mandarle una copia del comprobante."""

    cuit: str | None = None
    dni: str | None = None
    consumidor_final: bool = False
    nombre: str = ""
    domicilio: str = ""
    condicion_iva: int | None = None
    email: str | None = None

    def _to_dict(self) -> dict:
        d: dict = {
            "consumidor_final": self.consumidor_final,
            "nombre": self.nombre,
            "domicilio": self.domicilio,
        }
        if self.cuit is not None:
            d["cuit"] = self.cuit
        if self.dni is not None:
            d["dni"] = self.dni
        if self.condicion_iva is not None:
            d["condicion_iva"] = int(self.condicion_iva)
        if self.email is not None:
            d["email"] = self.email
        return d


@dataclass
class ComprobanteInput:
    """Body usado tal cual para `preview_comprobante`/`emitir_comprobante`, y con
    `comprobante_asociado` seteado para `preview_nota_credito`/`emitir_nota_credito`
    (el servidor exige ese campo ahí; el método del client es quien decide a qué
    endpoint pegarle según lo llames — este dataclass no valida esa regla, la deja
    pasar tal cual al servidor, que sí la exige).

    Los importes no se mandan sueltos -- `items` es la ÚNICA fuente: el neto, el
    IVA y el total salen de sumar los renglones. `concepto`/`forzar_cbte_tipo`:
    códigos de `arca_service_client.enums`, pasados como `int` tal cual (podés
    pasar el enum directo, `int(enum)` sale solo).

    `fecha`/`punto_venta`/`moneda` son opcionales: sin `fecha` el servidor usa la
    de hoy, sin `punto_venta` usa el de la credencial activa, sin `moneda` asume
    pesos."""

    idempotency_key: str
    concepto: int
    receptor: Receptor
    items: list[ItemFactura] = field(default_factory=list)
    punto_venta: int | None = None
    fecha: date | None = None
    fecha_serv_desde: date | None = None
    fecha_serv_hasta: date | None = None
    fecha_vto_pago: date | None = None
    moneda: str = "PES"
    forzar_cbte_tipo: int | None = None
    condicion_venta: str = "Contado"
    tributos: list[Tributo] = field(default_factory=list)
    opcionales: list[Opcional] = field(default_factory=list)
    comprobante_asociado: ComprobanteAsociado | None = None

    def to_payload(self) -> dict:
        payload: dict = {
            "idempotency_key": self.idempotency_key,
            "concepto": int(self.concepto),
            "receptor": self.receptor._to_dict(),
            "items": [it._to_dict() for it in self.items],
            "moneda": self.moneda,
            "condicion_venta": self.condicion_venta,
            "tributos": [t._to_dict() for t in self.tributos],
            "opcionales": [o._to_dict() for o in self.opcionales],
        }
        if self.punto_venta is not None:
            payload["punto_venta"] = self.punto_venta
        if self.fecha is not None:
            payload["fecha"] = self.fecha.isoformat()
        if self.fecha_serv_desde is not None:
            payload["fecha_serv_desde"] = self.fecha_serv_desde.isoformat()
        if self.fecha_serv_hasta is not None:
            payload["fecha_serv_hasta"] = self.fecha_serv_hasta.isoformat()
        if self.fecha_vto_pago is not None:
            payload["fecha_vto_pago"] = self.fecha_vto_pago.isoformat()
        if self.forzar_cbte_tipo is not None:
            payload["forzar_cbte_tipo"] = int(self.forzar_cbte_tipo)
        if self.comprobante_asociado is not None:
            payload["comprobante_asociado"] = self.comprobante_asociado._to_dict()
        return payload


@dataclass
class SesionEmbebidaInput:
    """Mismo body que `ComprobanteInput`, para
    `ArcaServiceClient.crear_sesion_embebida_comprobante`/`_nota_credito`/`_nota_debito`
    -- pero SIN `receptor`: eso lo completa el comprador dentro del `<iframe>`, no
    tu Plataforma. Por eso es un dataclass aparte y no `ComprobanteInput` con ese
    campo opcional -- `ComprobanteInput` lo exige a propósito para
    `emitir_comprobante` y compañía, y volverlo opcional ahí debilitaría esa
    validación para el camino que sí conoce al receptor.

    `comprobante_asociado` es obligatorio del lado servidor para
    `crear_sesion_embebida_nota_credito`/`_nota_debito`, igual que en
    `ComprobanteInput` -- ver su docstring."""

    idempotency_key: str
    concepto: int
    items: list[ItemFactura] = field(default_factory=list)
    punto_venta: int | None = None
    fecha: date | None = None
    fecha_serv_desde: date | None = None
    fecha_serv_hasta: date | None = None
    fecha_vto_pago: date | None = None
    moneda: str = "PES"
    forzar_cbte_tipo: int | None = None
    condicion_venta: str = "Contado"
    tributos: list[Tributo] = field(default_factory=list)
    opcionales: list[Opcional] = field(default_factory=list)
    comprobante_asociado: ComprobanteAsociado | None = None

    def to_payload(self) -> dict:
        payload: dict = {
            "idempotency_key": self.idempotency_key,
            "concepto": int(self.concepto),
            "items": [it._to_dict() for it in self.items],
            "moneda": self.moneda,
            "condicion_venta": self.condicion_venta,
            "tributos": [t._to_dict() for t in self.tributos],
            "opcionales": [o._to_dict() for o in self.opcionales],
        }
        if self.punto_venta is not None:
            payload["punto_venta"] = self.punto_venta
        if self.fecha is not None:
            payload["fecha"] = self.fecha.isoformat()
        if self.fecha_serv_desde is not None:
            payload["fecha_serv_desde"] = self.fecha_serv_desde.isoformat()
        if self.fecha_serv_hasta is not None:
            payload["fecha_serv_hasta"] = self.fecha_serv_hasta.isoformat()
        if self.fecha_vto_pago is not None:
            payload["fecha_vto_pago"] = self.fecha_vto_pago.isoformat()
        if self.forzar_cbte_tipo is not None:
            payload["forzar_cbte_tipo"] = int(self.forzar_cbte_tipo)
        if self.comprobante_asociado is not None:
            payload["comprobante_asociado"] = self.comprobante_asociado._to_dict()
        return payload


# ---------------------------------------------------------------------------
# Response — Cliente (onboarding por CUIT + vínculo)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OnboardingResult:
    """Respuesta de `ArcaServiceClient.por_cuit` — el `external_ref` a usar en TODO lo
    demás. Nunca lo elijas vos ni lo derives del CUIT: es un UUID que arca-service asigna,
    estable para ese CUIT sin importar cuántas Plataformas distintas lo hayan
    onboardeado."""

    external_ref: str

    @staticmethod
    def _from_json(d: dict) -> OnboardingResult:
        return OnboardingResult(external_ref=d["external_ref"])


@dataclass(frozen=True)
class BonificadoResult:
    """Respuesta de `ArcaServiceClient.set_bonificado` — el estado YA aplicado (no un eco
    ciego de lo que mandaste: si el vínculo ya estaba en el valor pedido, arca-service no
    hace nada y devuelve ese mismo valor, sin contar contra el límite de seguridad)."""

    bonificado: bool

    @staticmethod
    def _from_json(d: dict) -> BonificadoResult:
        return BonificadoResult(bonificado=d["bonificado"])


@dataclass(frozen=True)
class FacturacionResult:
    """Respuesta de `ArcaServiceClient.set_facturacion` — el `iibb`/`nombre_comercial` YA
    guardado (no un eco ciego de lo que mandaste). Razón social y domicilio NO están
    acá: los trae el padrón de AFIP, no se configuran por este medio.

    Cualquiera de los dos puede venir en `None`: un update parcial (mandar solo uno de
    los dos parámetros) no le pone valor por default al que quedó afuera -- si nunca se
    configuró, sigue en `None`."""

    iibb: str | None
    nombre_comercial: str | None

    @staticmethod
    def _from_json(d: dict) -> FacturacionResult:
        return FacturacionResult(iibb=d["iibb"], nombre_comercial=d["nombre_comercial"])


@dataclass(frozen=True)
class EmbedTokenResult:
    """Respuesta de `ArcaServiceClient.crear_embed_token` -- `embed_url` es un link
    PÚBLICO (nadie necesita mTLS ni tu API key para abrirlo) que vale hasta
    `expires_at` -- pensado para pasarle a TU frontend y que lo embeba en un
    `<iframe src="...">`, no para guardarlo ni reusarlo después de que venza. NO hace
    falta esto para mostrarle un comprobante a un usuario que ya está logueado en TU
    propio backend -- para eso alcanza con `get_comprobante_html`/`_pdf` llamado del
    lado servidor (ver el README, "Vista embebible (iframe)")."""

    embed_url: str
    expires_at: datetime

    @staticmethod
    def _from_json(d: dict) -> EmbedTokenResult:
        return EmbedTokenResult(
            embed_url=d["embed_url"], expires_at=_parse_fecha_hora(d["expires_at"])
        )


@dataclass(frozen=True)
class ConexionAfipEmbedTokenResult:
    """Respuesta de `ArcaServiceClient.crear_conexion_afip_embed_token` -- mismo patrón
    que `EmbedTokenResult` (`embed_url` público, vale hasta `expires_at`), pero
    `embed_url` acá apunta a un flujo INTERACTIVO (generar/completar/importar la
    credencial, elegir cuál usar) en vez de una vista de solo lectura -- pensado para
    `<iframe src="...">` en el frontend de tu Plataforma, para que tu cliente final
    gestione su propia conexión AFIP sin loguearse en arca-service ni ver nada de este
    SDK ni de tu backend en el medio. Ver el README, "Conexión AFIP embebida (iframe)"."""

    embed_url: str
    expires_at: datetime

    @staticmethod
    def _from_json(d: dict) -> ConexionAfipEmbedTokenResult:
        return ConexionAfipEmbedTokenResult(
            embed_url=d["embed_url"], expires_at=_parse_fecha_hora(d["expires_at"])
        )


# ---------------------------------------------------------------------------
# Response — onboarding de credencial
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GenerarCsrResult:
    csr_pem: str
    alias: str

    @staticmethod
    def _from_json(d: dict) -> GenerarCsrResult:
        return GenerarCsrResult(csr_pem=d["csr_pem"], alias=d["alias"])


@dataclass(frozen=True)
class CredencialResult:
    point_of_sale: int
    active: bool

    @staticmethod
    def _from_json(d: dict) -> CredencialResult:
        return CredencialResult(point_of_sale=d["point_of_sale"], active=d["active"])


@dataclass(frozen=True)
class Chequeo:
    check: str
    ok: bool
    bloqueante: bool
    mensaje: str


@dataclass(frozen=True)
class DiagnosticoResult:
    chequeos: tuple[Chequeo, ...]
    listo: bool

    @staticmethod
    def _from_json(d: dict) -> DiagnosticoResult:
        chequeos = tuple(
            Chequeo(check=c["check"], ok=c["ok"], bloqueante=c["bloqueante"], mensaje=c["mensaje"])
            for c in d["chequeos"]
        )
        return DiagnosticoResult(chequeos=chequeos, listo=d["listo"])


@dataclass(frozen=True)
class PuntoVentaHabilitado:
    nro: int
    emision_tipo: str | None = None


@dataclass(frozen=True)
class PuntoVentaExcluido:
    nro: int
    motivo: str
    raw_emision_tipo: str | None = None


@dataclass(frozen=True)
class PuntosVentaResult:
    habilitados: tuple[PuntoVentaHabilitado, ...]
    excluidos: tuple[PuntoVentaExcluido, ...]

    @staticmethod
    def _from_json(d: dict) -> PuntosVentaResult:
        habilitados = tuple(
            PuntoVentaHabilitado(nro=h["nro"], emision_tipo=h.get("emision_tipo"))
            for h in d["habilitados"]
        )
        excluidos = tuple(
            PuntoVentaExcluido(
                nro=e["nro"], motivo=e["motivo"], raw_emision_tipo=e.get("raw_emision_tipo")
            )
            for e in d["excluidos"]
        )
        return PuntosVentaResult(habilitados=habilitados, excluidos=excluidos)


# ---------------------------------------------------------------------------
# Response — padrón (consultar_padron)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Actividad:
    id_actividad: int
    descripcion: str
    periodo: int
    orden: int
    nomenclador: int = 0


@dataclass(frozen=True)
class Impuesto:
    id_impuesto: int
    descripcion: str
    estado: str
    periodo: int
    motivo: str = ""


@dataclass(frozen=True)
class Domicilio:
    direccion: str
    localidad: str
    provincia: str
    id_provincia: int
    codigo_postal: str
    tipo_domicilio: str
    dato_adicional: str = ""
    tipo_dato_adicional: str = ""


@dataclass(frozen=True)
class Dependencia:
    id_dependencia: int
    descripcion: str
    direccion: str
    localidad: str
    provincia: str
    id_provincia: int
    codigo_postal: str


@dataclass(frozen=True)
class Categoria:
    descripcion: str
    id_categoria: int
    id_impuesto: int
    periodo: int


@dataclass(frozen=True)
class Regimen:
    id_regimen: int
    descripcion: str
    tipo: str
    id_impuesto: int
    periodo: int


@dataclass(frozen=True)
class Caracterizacion:
    id_caracterizacion: int
    descripcion: str
    periodo: int
    fecha_solicitud: int = 0


@dataclass(frozen=True)
class ComponenteSociedad:
    id_persona_asociada: int
    nombre: str
    apellido: str
    razon_social: str
    tipo_componente: str
    fecha_relacion: datetime | None = None
    fecha_vencimiento: datetime | None = None


def _domicilio(d: dict | None) -> Domicilio | None:
    return Domicilio(**d) if d else None


def _dependencia(d: dict | None) -> Dependencia | None:
    return Dependencia(**d) if d else None


def _categoria(d: dict | None) -> Categoria | None:
    return Categoria(**d) if d else None


@dataclass(frozen=True)
class PersonaArca:
    """Espejo de `PersonaArcaOut` — respuesta de `consultar_padron`."""

    cuit: str
    razon_social: str
    nombre: str
    apellido: str
    domicilio: str
    condicion_iva: str
    estado_clave: str
    tipo_persona: str = ""
    categoria_monotributo: str = ""
    actividades: tuple[Actividad, ...] = ()
    impuestos: tuple[Impuesto, ...] = ()
    tipo_clave: str = ""
    mes_cierre: int = 0
    es_sucesion: str = ""
    fecha_contrato_social: datetime | None = None
    fecha_fallecimiento: datetime | None = None
    domicilio_fiscal: Domicilio | None = None
    dependencia: Dependencia | None = None
    caracterizaciones: tuple[Caracterizacion, ...] = ()
    categoria_monotributo_detalle: Categoria | None = None
    categoria_autonomo: Categoria | None = None
    regimenes: tuple[Regimen, ...] = ()
    componentes_sociedad: tuple[ComponenteSociedad, ...] = ()

    @staticmethod
    def _from_json(d: dict) -> PersonaArca:
        return PersonaArca(
            cuit=d["cuit"],
            razon_social=d["razon_social"],
            nombre=d["nombre"],
            apellido=d["apellido"],
            domicilio=d["domicilio"],
            condicion_iva=d["condicion_iva"],
            estado_clave=d["estado_clave"],
            tipo_persona=d.get("tipo_persona", ""),
            categoria_monotributo=d.get("categoria_monotributo", ""),
            actividades=tuple(Actividad(**a) for a in d.get("actividades", [])),
            impuestos=tuple(Impuesto(**i) for i in d.get("impuestos", [])),
            tipo_clave=d.get("tipo_clave", ""),
            mes_cierre=d.get("mes_cierre", 0),
            es_sucesion=d.get("es_sucesion", ""),
            fecha_contrato_social=_fecha_hora(d.get("fecha_contrato_social")),
            fecha_fallecimiento=_fecha_hora(d.get("fecha_fallecimiento")),
            domicilio_fiscal=_domicilio(d.get("domicilio_fiscal")),
            dependencia=_dependencia(d.get("dependencia")),
            caracterizaciones=tuple(Caracterizacion(**c) for c in d.get("caracterizaciones", [])),
            categoria_monotributo_detalle=_categoria(d.get("categoria_monotributo_detalle")),
            categoria_autonomo=_categoria(d.get("categoria_autonomo")),
            regimenes=tuple(Regimen(**r) for r in d.get("regimenes", [])),
            componentes_sociedad=tuple(
                ComponenteSociedad(
                    id_persona_asociada=c["id_persona_asociada"],
                    nombre=c["nombre"],
                    apellido=c["apellido"],
                    razon_social=c["razon_social"],
                    tipo_componente=c["tipo_componente"],
                    fecha_relacion=_fecha_hora(c.get("fecha_relacion")),
                    fecha_vencimiento=_fecha_hora(c.get("fecha_vencimiento")),
                )
                for c in d.get("componentes_sociedad", [])
            ),
        )


# ---------------------------------------------------------------------------
# Response — preview/emisión
#
# `comprobante`/`importes` (y, solo en una emisión real, `receptor`) son objetos
# anidados, no campos planos -- confirmado contra el comportamiento real de la API
# (no solo contra la documentación), incluyendo que el webhook manda el mismo
# documento que la respuesta de la API. Ver `tests/test_contract.py` para el fixture
# completo con los valores verificados.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CodigoAfip:
    """Un código cerrado de AFIP con su nombre al lado -- ej. `{"codigo": 96,
    "descripcion": "DNI"}`. `descripcion` es `None` para un código que la tabla no
    reconoce (una fila vieja con un código que AFIP retiró después), nunca un
    `KeyError`: un comprobante ya emitido no puede dejar de poder consultarse porque
    cambiaron las tablas."""

    codigo: int
    descripcion: str | None


@dataclass(frozen=True)
class CondicionIvaReceptor:
    """La condición frente al IVA del receptor, con quién la decidió. `fuente`:
    `"padron"` en el caso normal, `"declarada"` si el padrón no pudo clasificar ese
    CUIT y valió lo que mandó el caller."""

    codigo: int
    descripcion: str | None
    fuente: str | None = None


@dataclass(frozen=True)
class ComprobanteInfo:
    """Qué comprobante es: tipo, letra fiscal, código de AFIP y ubicación --
    `comprobante` tanto en `PreviewResult` como en `EmisionResult`.

    `letra`/`codigo_afip` son `None` mientras una emisión real está `pending` (se
    resuelven recién al pedir el CAE) y siempre vienen resueltos en un preview.
    `punto_venta`/`numero` no existen todavía en un preview -- nada se emitió --, por
    eso son opcionales acá aunque una emisión real siempre los traiga (con `numero` en
    `None` hasta `issued`)."""

    tipo: str
    letra: str | None = None
    codigo_afip: int | None = None
    punto_venta: int | None = None
    numero: int | None = None
    fecha: date | None = None

    @staticmethod
    def _from_json(d: dict) -> ComprobanteInfo:
        return ComprobanteInfo(
            tipo=d["tipo"],
            letra=d.get("letra"),
            codigo_afip=d.get("codigo_afip"),
            punto_venta=d.get("punto_venta"),
            numero=d.get("numero"),
            fecha=_fecha(d.get("fecha")),
        )


@dataclass(frozen=True)
class Importes:
    """Los importes, la moneda y la cotización -- `importes` tanto en `PreviewResult`
    como en `EmisionResult`. Van como STRING en el JSON y se parsean a `Decimal`
    (nunca `float`, evita sorpresas de representación binaria en un monto).

    `moneda`/`cotizacion` son `None` en un preview -- no vienen en ese sub-objeto ahí --,
    siempre presentes en una emisión real."""

    neto: Decimal
    iva: Decimal
    no_gravado: Decimal
    exento: Decimal
    tributos: Decimal
    total: Decimal
    moneda: str | None = None
    cotizacion: Decimal | None = None

    @staticmethod
    def _from_json(d: dict) -> Importes:
        return Importes(
            neto=_dec(d["neto"]),
            iva=_dec(d["iva"]),
            no_gravado=_dec(d["no_gravado"]),
            exento=_dec(d["exento"]),
            tributos=_dec(d["tributos"]),
            total=_dec(d["total"]),
            moneda=d.get("moneda"),
            cotizacion=_dec(d["cotizacion"]) if d.get("cotizacion") is not None else None,
        )


@dataclass(frozen=True)
class ReceptorInfo:
    """A quién se le facturó -- solo en `EmisionResult` (un preview no confirma
    receptor todavía). `doc_nro` viaja como número en el JSON, no como string."""

    doc_tipo: CodigoAfip | None
    doc_nro: int | None
    nombre: str
    domicilio: str
    condicion_iva: CondicionIvaReceptor | None

    @staticmethod
    def _from_json(d: dict) -> ReceptorInfo:
        doc_tipo = d.get("doc_tipo")
        condicion_iva = d.get("condicion_iva")
        return ReceptorInfo(
            doc_tipo=CodigoAfip(**doc_tipo) if doc_tipo else None,
            doc_nro=d.get("doc_nro"),
            nombre=d.get("nombre", ""),
            domicilio=d.get("domicilio", ""),
            condicion_iva=CondicionIvaReceptor(**condicion_iva) if condicion_iva else None,
        )


@dataclass(frozen=True)
class PreviewResult:
    comprobante: ComprobanteInfo
    importes: Importes

    @staticmethod
    def _from_json(d: dict) -> PreviewResult:
        return PreviewResult(
            comprobante=ComprobanteInfo._from_json(d["comprobante"]),
            importes=Importes._from_json(d["importes"]),
        )


@dataclass(frozen=True)
class EmisionResult:
    """Espejo de `EmisionOut`. `estado`: `"pending"` recién creada, `"issued"` con
    `comprobante.numero`/`cae`/`cae_vencimiento`/`qr_url` ya completos, o `"error"` con
    `errores` poblado — pollear `ArcaServiceClient.get_comprobante` hasta que deje de
    ser `"pending"` (o esperar el webhook, si lo configuraste). Mirá SIEMPRE `estado`
    para saber si ya está listo: `importes` se calcula desde que la emisión se crea, así
    que no hay ningún importe en cero mientras está `pending` que sirva de proxy.

    `observaciones`: comentarios de AFIP sobre un comprobante que SÍ autorizó (ej. el
    documento del receptor no figura en el padrón, una fecha al límite) -- a diferencia
    de `errores`, no bloquean nada ni cambian `estado`; vale la pena mostrárselos a quien
    emitió en vez de descartarlos."""

    id: str
    idempotency_key: str
    estado: str
    comprobante: ComprobanteInfo
    importes: Importes
    receptor: ReceptorInfo
    cae: str = ""
    cae_vencimiento: date | None = None
    qr_url: str = ""
    errores: tuple[AfipErrorDetail, ...] | None = None
    observaciones: list[str] | None = None
    webhook_delivered: bool | None = None
    webhook_last_error: str = ""

    @staticmethod
    def _from_json(d: dict) -> EmisionResult:
        errores = d.get("errores")
        return EmisionResult(
            id=d["id"],
            idempotency_key=d["idempotency_key"],
            estado=d["estado"],
            comprobante=ComprobanteInfo._from_json(d["comprobante"]),
            importes=Importes._from_json(d["importes"]),
            receptor=ReceptorInfo._from_json(d["receptor"]),
            cae=d.get("cae", ""),
            cae_vencimiento=_fecha(d.get("cae_vencimiento")),
            qr_url=d.get("qr_url", ""),
            errores=(
                tuple(AfipErrorDetail(codigo=e["codigo"], mensaje=e["mensaje"]) for e in errores)
                if errores is not None
                else None
            ),
            observaciones=d.get("observaciones"),
            webhook_delivered=d.get("webhook_delivered"),
            webhook_last_error=d.get("webhook_last_error", ""),
        )


@dataclass(frozen=True)
class ListaComprobantesResult:
    """Respuesta de `ArcaServiceClient.listar_comprobantes` -- `items`, más nuevo primero,
    con el mismo shape que `EmisionResult`. `count` es el total que matchea los filtros
    (para paginar con `limit`/`offset`), no `len(items)` -- son iguales solo cuando todo
    entra en una página."""

    items: tuple[EmisionResult, ...]
    count: int

    @staticmethod
    def _from_json(d: dict) -> ListaComprobantesResult:
        return ListaComprobantesResult(
            items=tuple(EmisionResult._from_json(item) for item in d["items"]),
            count=d["count"],
        )


@dataclass(frozen=True)
class SesionEmbebidaResult:
    """Respuesta 201 de `ArcaServiceClient.crear_sesion_embebida_comprobante`/
    `_nota_credito`/`_nota_debito` -- `embed_url` es un link PÚBLICO (sin mTLS/API key,
    listo para `<iframe src="...">`) donde el comprador completa sus propios datos;
    vale hasta `expires_at` (30 min por default del lado servidor). Mismo trade-off de
    vida corta que `EmbedTokenResult`/`ConexionAfipEmbedTokenResult` -- tratalo como un
    secreto de corta vida, no lo loggees ni lo guardes más tiempo del que dure."""

    embed_url: str
    expires_at: datetime

    @staticmethod
    def _from_json(d: dict) -> SesionEmbebidaResult:
        return SesionEmbebidaResult(
            embed_url=d["embed_url"], expires_at=_parse_fecha_hora(d["expires_at"])
        )


# ---------------------------------------------------------------------------
# Response — lote (emitir_lote_comprobantes/emitir_lote_notas_credito/
# emitir_lote_notas_debito)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LoteItemResult:
    """Resultado de UN ítem del lote — fallo parcial, no todo-o-nada: un ítem con
    `idempotency_key` en conflicto (409 si fuera individual) o similar no aborta a los
    demás, así que cada uno se resuelve por separado. `ok=False` trae `error`/
    `status_code` poblados en vez de `emision` (espejo de `LoteItemOut`)."""

    idempotency_key: str
    ok: bool
    emision: EmisionResult | None = None
    error: str | None = None
    status_code: int | None = None

    @staticmethod
    def _from_json(d: dict) -> LoteItemResult:
        emision_json = d.get("emision")
        return LoteItemResult(
            idempotency_key=d["idempotency_key"],
            ok=d["ok"],
            emision=EmisionResult._from_json(emision_json) if emision_json is not None else None,
            error=d.get("error"),
            status_code=d.get("status_code"),
        )
