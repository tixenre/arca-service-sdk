"""arca_service_client.models — dataclasses de request/response, espejo exacto de
`lib/arca_service_phx_web/schemas/*.ex` (tixenre/arca-service, stack Phoenix). Los de
REQUEST (`ComprobanteInput` y sus componentes) saben serializarse a sí mismos
(`to_payload()`) al shape JSON que la API
espera — Decimal -> string (nunca un `float` crudo: evita sorpresas de representación
binaria en un monto), `date` -> ISO. Los de RESPONSE (`EmisionResult`, `PersonaArca`,
etc.) tienen un `_from_json` que parsea el dict que devuelve `response.json()` — nunca
levantan por un campo extra que la API agregue después (`dict.get`), sí levantan
`KeyError` por uno FALTANTE que el schema real marca como obligatorio (fail loud, no
fabricar un default para un campo que se supone que siempre viene)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any


def _dec(v: Any) -> Decimal:
    return v if isinstance(v, Decimal) else Decimal(str(v))


def _fecha(v: Any) -> date | None:
    return date.fromisoformat(v) if v else None


def _fecha_hora(v: Any) -> datetime | None:
    return datetime.fromisoformat(v) if v else None


# ---------------------------------------------------------------------------
# Request — comprobante a emitir/previsualizar
# ---------------------------------------------------------------------------


@dataclass
class ItemIva:
    """Desglose de neto por alícuota — ver
    `lib/arca_service_phx_web/schemas/item_iva_in.ex`. `alicuota_id`: id de
    `arca_service_client.enums.Alicuota`."""

    alicuota_id: int
    base_imponible: Decimal

    def _to_dict(self) -> dict:
        return {"alicuota_id": int(self.alicuota_id), "base_imponible": str(self.base_imponible)}


@dataclass
class Tributo:
    """Un tributo/percepción (Impuestos Internos, percepciones de IIBB, etc.) — ver
    `TributoIn`. `id`: código de la tabla AFIP `FEParamGetTiposTributos`."""

    id: int
    base_imponible: Decimal
    alicuota_pct: Decimal
    importe: Decimal
    desc: str = ""

    def _to_dict(self) -> dict:
        return {
            "id": self.id,
            "base_imponible": str(self.base_imponible),
            "alicuota_pct": str(self.alicuota_pct),
            "importe": str(self.importe),
            "desc": self.desc,
        }


@dataclass
class Opcional:
    """Un dato opcional del comprobante (ej. CBU/Alias de una FCE MiPyme) — ver
    `OpcionalIn`."""

    id: str
    valor: str

    def _to_dict(self) -> dict:
        return {"id": self.id, "valor": self.valor}


@dataclass
class ItemFactura:
    """Línea comercial — puramente para el render del comprobante (.html/.pdf/.imagen),
    no participa del cálculo fiscal. Ver `ItemFacturaIn`."""

    descripcion: str
    precio_unitario: Decimal
    subtotal: Decimal
    codigo: str = ""
    cantidad: Decimal = Decimal("1")
    unidad_medida: str = "unidad"
    bonificacion_pct: Decimal = Decimal("0")
    detalle: str = ""

    def _to_dict(self) -> dict:
        return {
            "descripcion": self.descripcion,
            "precio_unitario": str(self.precio_unitario),
            "subtotal": str(self.subtotal),
            "codigo": self.codigo,
            "cantidad": str(self.cantidad),
            "unidad_medida": self.unidad_medida,
            "bonificacion_pct": str(self.bonificacion_pct),
            "detalle": self.detalle,
        }


@dataclass
class ComprobanteAsociado:
    """Referencia a la factura original — obligatorio en una nota de crédito/débito
    (`ComprobanteInput.comprobante_asociado`). Ver `ComprobanteAsociadoIn`."""

    tipo: int
    punto_venta: int
    numero: int
    cuit: str | None = None
    fecha: date | None = None

    def _to_dict(self) -> dict:
        d: dict = {"tipo": int(self.tipo), "punto_venta": self.punto_venta, "numero": self.numero}
        if self.cuit is not None:
            d["cuit"] = self.cuit
        if self.fecha is not None:
            d["fecha"] = self.fecha.isoformat()
        return d


@dataclass
class ComprobanteInput:
    """Espejo de `ComprobanteBaseIn` — usado tal cual para `preview_comprobante`/
    `emitir_comprobante`, y con `comprobante_asociado` seteado para
    `preview_nota_credito`/`emitir_nota_credito` (arca-service exige ese campo ahí, ver
    `NotaCreditoIn`/`NotaDebitoIn`; el método del client es quien decide a qué endpoint
    pegarle según lo llames — este dataclass no valida esa regla, la deja pasar tal cual
    al servidor, que sí la exige).

    `concepto`/`emisor_condicion_iva`/`receptor_doc_tipo`/`receptor_condicion_iva`/
    `forzar_cbte_tipo`: códigos de `arca_service_client.enums`, pasados como `int` tal
    cual (podés pasar el enum directo, `int(enum)` sale solo)."""

    idempotency_key: str
    concepto: int
    emisor_condicion_iva: int
    receptor_doc_tipo: int
    receptor_doc_nro: str
    receptor_condicion_iva: int
    fecha: date
    punto_venta: int | None = None
    fecha_serv_desde: date | None = None
    fecha_serv_hasta: date | None = None
    fecha_vto_pago: date | None = None
    moneda: str = "PES"
    cotizacion: Decimal = Decimal("1")
    importe_neto: Decimal = Decimal("0")
    importe_no_gravado: Decimal = Decimal("0")
    importe_exento: Decimal = Decimal("0")
    alicuota_unica: int | None = None
    items_iva: list[ItemIva] = field(default_factory=list)
    tributos: list[Tributo] = field(default_factory=list)
    opcionales: list[Opcional] = field(default_factory=list)
    forzar_cbte_tipo: int | None = None
    items: list[ItemFactura] = field(default_factory=list)
    emisor_razon_social: str = ""
    emisor_domicilio: str = ""
    emisor_iibb: str = ""
    receptor_nombre: str = ""
    receptor_domicilio: str = ""
    condicion_venta: str = "Contado"
    comprobante_asociado: ComprobanteAsociado | None = None

    def to_payload(self) -> dict:
        payload: dict = {
            "idempotency_key": self.idempotency_key,
            "concepto": int(self.concepto),
            "emisor_condicion_iva": int(self.emisor_condicion_iva),
            "receptor_doc_tipo": int(self.receptor_doc_tipo),
            "receptor_doc_nro": self.receptor_doc_nro,
            "receptor_condicion_iva": int(self.receptor_condicion_iva),
            "fecha": self.fecha.isoformat(),
            "moneda": self.moneda,
            "cotizacion": str(self.cotizacion),
            "importe_neto": str(self.importe_neto),
            "importe_no_gravado": str(self.importe_no_gravado),
            "importe_exento": str(self.importe_exento),
            "items_iva": [i._to_dict() for i in self.items_iva],
            "tributos": [t._to_dict() for t in self.tributos],
            "opcionales": [o._to_dict() for o in self.opcionales],
            "items": [it._to_dict() for it in self.items],
            "emisor_razon_social": self.emisor_razon_social,
            "emisor_domicilio": self.emisor_domicilio,
            "emisor_iibb": self.emisor_iibb,
            "receptor_nombre": self.receptor_nombre,
            "receptor_domicilio": self.receptor_domicilio,
            "condicion_venta": self.condicion_venta,
        }
        if self.punto_venta is not None:
            payload["punto_venta"] = self.punto_venta
        if self.fecha_serv_desde is not None:
            payload["fecha_serv_desde"] = self.fecha_serv_desde.isoformat()
        if self.fecha_serv_hasta is not None:
            payload["fecha_serv_hasta"] = self.fecha_serv_hasta.isoformat()
        if self.fecha_vto_pago is not None:
            payload["fecha_vto_pago"] = self.fecha_vto_pago.isoformat()
        if self.alicuota_unica is not None:
            payload["alicuota_unica"] = int(self.alicuota_unica)
        if self.forzar_cbte_tipo is not None:
            payload["forzar_cbte_tipo"] = int(self.forzar_cbte_tipo)
        if self.comprobante_asociado is not None:
            payload["comprobante_asociado"] = self.comprobante_asociado._to_dict()
        return payload


# ---------------------------------------------------------------------------
# Response — Cliente (onboarding por CUIT + vínculo, Fase 12)
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
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PreviewResult:
    cbte_tipo: int
    cbte_letra: str
    importe_neto: Decimal
    importe_iva: Decimal
    importe_total: Decimal
    importe_no_gravado: Decimal
    importe_exento: Decimal
    importe_tributos: Decimal

    @staticmethod
    def _from_json(d: dict) -> PreviewResult:
        return PreviewResult(
            cbte_tipo=d["cbte_tipo"],
            cbte_letra=d["cbte_letra"],
            importe_neto=_dec(d["importe_neto"]),
            importe_iva=_dec(d["importe_iva"]),
            importe_total=_dec(d["importe_total"]),
            importe_no_gravado=_dec(d["importe_no_gravado"]),
            importe_exento=_dec(d["importe_exento"]),
            importe_tributos=_dec(d["importe_tributos"]),
        )


@dataclass(frozen=True)
class EmisionResult:
    """Espejo de `EmisionOut`. `estado`: `"pending"` recién creada, `"issued"` con
    `numero`/`cae`/`cae_vencimiento`/`qr_url` ya completos, o `"error"` con `errores`
    poblado — pollear `ArcaServiceClient.get_comprobante` hasta que deje de ser
    `"pending"` (o esperar el webhook, si lo configuraste)."""

    id: str
    idempotency_key: str
    tipo: str
    estado: str
    numero: int | None = None
    cae: str = ""
    cae_vencimiento: date | None = None
    qr_url: str = ""
    errores: list | None = None
    webhook_delivered: bool | None = None
    webhook_last_error: str = ""

    @staticmethod
    def _from_json(d: dict) -> EmisionResult:
        return EmisionResult(
            id=d["id"],
            idempotency_key=d["idempotency_key"],
            tipo=d["tipo"],
            estado=d["estado"],
            numero=d.get("numero"),
            cae=d.get("cae", ""),
            cae_vencimiento=_fecha(d.get("cae_vencimiento")),
            qr_url=d.get("qr_url", ""),
            errores=d.get("errores"),
            webhook_delivered=d.get("webhook_delivered"),
            webhook_last_error=d.get("webhook_last_error", ""),
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
