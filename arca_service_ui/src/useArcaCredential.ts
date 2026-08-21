import { useCallback, useRef, useState } from "react";

import type {
  ArcaServiceBackend,
  AsyncSlot,
  CredencialResult,
  DiagnosticoResult,
  GenerarCsrResult,
  OnboardingResult,
  PuntosVentaResult,
  UseArcaCredentialResult,
} from "./types";

function toError(err: unknown): Error {
  return err instanceof Error ? err : new Error(String(err));
}

/**
 * Un slot de estado async con protección contra respuestas fuera de orden: si se
 * dispara una segunda llamada antes de que la primera resuelva (doble click, o
 * `completarCredencial` seguido de `importarCredencial` sobre el mismo slot
 * `credencial`), la resolución de la primera NO debe pisar el estado de la segunda —
 * un contador de "generación" descarta cualquier resultado que llegue después de que
 * ya arrancó una llamada más nueva.
 */
function useAsyncSlot<T>(): [AsyncSlot<T>, (fn: () => Promise<T>) => Promise<void>, () => void] {
  const [slot, setSlot] = useState<AsyncSlot<T>>({ status: "idle" });
  const generation = useRef(0);

  const run = useCallback(async (fn: () => Promise<T>) => {
    const myGeneration = ++generation.current;
    setSlot({ status: "loading" });
    try {
      const data = await fn();
      if (generation.current === myGeneration) {
        setSlot({ status: "ready", data });
      }
    } catch (err) {
      if (generation.current === myGeneration) {
        setSlot({ status: "error", error: toError(err) });
      }
    }
  }, []);

  const reset = useCallback(() => {
    generation.current += 1; // invalida cualquier llamada en vuelo
    setSlot({ status: "idle" });
  }, []);

  return [slot, run, reset];
}

/**
 * Onboarding de credencial de arca-service — 5 slots de estado independientes
 * (`onboarding`/`csr`/`credencial`/`diagnostico`/`puntosVenta`), cada uno maneja su
 * propio ciclo `idle -> loading -> ready|error` sin pisar a los demás.
 *
 * Headless a propósito: no renderiza nada, no importa CSS — el producto arma su propia
 * UI arriba de este estado. Un wizard de varios pasos con estilos propios de este
 * paquete se nota "pegado" en una UI ajena; un bloque de estado no.
 */
export function useArcaCredential(backend: ArcaServiceBackend): UseArcaCredentialResult {
  const [onboarding, runOnboarding, resetOnboarding] = useAsyncSlot<OnboardingResult>();
  const [csr, runCsr, resetCsr] = useAsyncSlot<GenerarCsrResult>();
  const [credencial, runCredencial, resetCredencial] = useAsyncSlot<CredencialResult>();
  const [diagnostico, runDiagnostico, resetDiagnostico] = useAsyncSlot<DiagnosticoResult>();
  const [puntosVenta, runPuntosVenta, resetPuntosVenta] = useAsyncSlot<PuntosVentaResult>();

  const porCuit = useCallback(
    (cuit: string) => runOnboarding(() => backend.porCuit(cuit)),
    [backend, runOnboarding],
  );

  const generarCsr = useCallback(
    (cuit: string, regenerar = false) => runCsr(() => backend.generarCsr(cuit, regenerar)),
    [backend, runCsr],
  );

  const completarCredencial = useCallback(
    (certPem: string, pointOfSale = 0) =>
      runCredencial(() => backend.completarCredencial(certPem, pointOfSale)),
    [backend, runCredencial],
  );

  const importarCredencial = useCallback(
    (cuit: string, certPem: string, keyPem: string, keyPassword?: string, pointOfSale = 0) =>
      runCredencial(() =>
        backend.importarCredencial(cuit, certPem, keyPem, keyPassword, pointOfSale),
      ),
    [backend, runCredencial],
  );

  const diagnosticar = useCallback(
    () => runDiagnostico(() => backend.diagnosticar()),
    [backend, runDiagnostico],
  );

  const listarPuntosDeVenta = useCallback(
    () => runPuntosVenta(() => backend.listarPuntosDeVenta()),
    [backend, runPuntosVenta],
  );

  const reset = useCallback(() => {
    resetOnboarding();
    resetCsr();
    resetCredencial();
    resetDiagnostico();
    resetPuntosVenta();
  }, [resetOnboarding, resetCsr, resetCredencial, resetDiagnostico, resetPuntosVenta]);

  return {
    onboarding,
    csr,
    credencial,
    diagnostico,
    puntosVenta,
    porCuit,
    generarCsr,
    completarCredencial,
    importarCredencial,
    diagnosticar,
    listarPuntosDeVenta,
    reset,
  };
}
