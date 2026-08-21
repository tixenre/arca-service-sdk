/**
 * Tipos espejo de las respuestas de arca-service (ver apps/arca/schemas.py de
 * tixenre/arca-service, y arca_service_client.models del lado Python) — en camelCase,
 * la convención TS/JS habitual. El backend del PRODUCTO (quien implementa
 * `ArcaServiceBackend`) es responsable de convertir la respuesta snake_case de
 * arca-service a este shape antes de devolverla acá; este paquete no habla HTTP.
 */

export interface GenerarCsrResult {
  csrPem: string;
  alias: string;
}

export interface CredencialResult {
  pointOfSale: number;
  active: boolean;
}

export interface Chequeo {
  check: string;
  ok: boolean;
  bloqueante: boolean;
  mensaje: string;
}

export interface DiagnosticoResult {
  chequeos: Chequeo[];
  listo: boolean;
}

export interface PuntoVentaHabilitado {
  nro: number;
  emisionTipo?: string;
}

export interface PuntoVentaExcluido {
  nro: number;
  motivo: string;
  rawEmisionTipo?: string;
}

export interface PuntosVentaResult {
  habilitados: PuntoVentaHabilitado[];
  excluidos: PuntoVentaExcluido[];
}

/**
 * Contrato que el producto implementa contra SU PROPIO backend (que a su vez le pega a
 * arca-service, típicamente vía `arca_service_client` si es Python) — mapeo 1:1 con los
 * métodos de `ArcaServiceClient`, sin `external_ref` explícito: el backend del producto
 * ya sabe de qué org se trata por la sesión del usuario, no es responsabilidad de este
 * hook resolverlo.
 */
export interface ArcaServiceBackend {
  generarCsr(cuit: string, regenerar?: boolean): Promise<GenerarCsrResult>;
  completarCredencial(certPem: string, pointOfSale?: number): Promise<CredencialResult>;
  importarCredencial(
    cuit: string,
    certPem: string,
    keyPem: string,
    keyPassword?: string,
    pointOfSale?: number,
  ): Promise<CredencialResult>;
  diagnosticar(): Promise<DiagnosticoResult>;
  listarPuntosDeVenta(): Promise<PuntosVentaResult>;
}

/**
 * Estado de una operación async — discriminado por `status` para que TS angoste
 * `data`/`error` según corresponda (no un objeto con los dos campos siempre opcionales
 * y sin relación con `status`, que dejaría leer `.data` sin haber chequeado nada).
 */
export type AsyncSlot<T> =
  | { status: "idle" }
  | { status: "loading" }
  | { status: "ready"; data: T }
  | { status: "error"; error: Error };

export interface UseArcaCredentialResult {
  csr: AsyncSlot<GenerarCsrResult>;
  /** `completarCredencial` e `importarCredencial` escriben acá los dos — son los dos
   * caminos hacia el mismo resultado (ver docstring de `ArcaServiceBackend` en
   * arca_service_client). */
  credencial: AsyncSlot<CredencialResult>;
  diagnostico: AsyncSlot<DiagnosticoResult>;
  puntosVenta: AsyncSlot<PuntosVentaResult>;

  generarCsr(cuit: string, regenerar?: boolean): Promise<void>;
  completarCredencial(certPem: string, pointOfSale?: number): Promise<void>;
  importarCredencial(
    cuit: string,
    certPem: string,
    keyPem: string,
    keyPassword?: string,
    pointOfSale?: number,
  ): Promise<void>;
  diagnosticar(): Promise<void>;
  listarPuntosDeVenta(): Promise<void>;

  /** Vuelve los 4 slots a `idle` — e invalida cualquier llamada todavía en vuelo, para
   * que su resolución tardía no pise el estado recién reseteado (ver
   * `useAsyncSlot` en useArcaCredential.ts). */
  reset(): void;
}
