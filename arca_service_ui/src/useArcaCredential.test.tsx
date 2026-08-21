import { act, renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { useArcaCredential } from "./useArcaCredential";
import type { ArcaServiceBackend } from "./types";

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (err: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

function makeBackend(overrides: Partial<ArcaServiceBackend> = {}): ArcaServiceBackend {
  return {
    porCuit: vi.fn().mockResolvedValue({ externalRef: "ext-1" }),
    generarCsr: vi.fn().mockResolvedValue({ csrPem: "csr", alias: "alias-1" }),
    completarCredencial: vi.fn().mockResolvedValue({ pointOfSale: 0, active: true }),
    importarCredencial: vi.fn().mockResolvedValue({ pointOfSale: 0, active: true }),
    diagnosticar: vi.fn().mockResolvedValue({ chequeos: [], listo: true }),
    listarPuntosDeVenta: vi.fn().mockResolvedValue({ habilitados: [], excluidos: [] }),
    ...overrides,
  };
}

describe("useArcaCredential", () => {
  it("arranca con los 5 slots en idle", () => {
    const { result } = renderHook(() => useArcaCredential(makeBackend()));
    expect(result.current.onboarding.status).toBe("idle");
    expect(result.current.csr.status).toBe("idle");
    expect(result.current.credencial.status).toBe("idle");
    expect(result.current.diagnostico.status).toBe("idle");
    expect(result.current.puntosVenta.status).toBe("idle");
  });

  it("porCuit: idle -> loading -> ready con el external_ref", async () => {
    const { promise, resolve } = deferred<{ externalRef: string }>();
    const backend = makeBackend({ porCuit: vi.fn().mockReturnValue(promise) });
    const { result } = renderHook(() => useArcaCredential(backend));

    act(() => {
      void result.current.porCuit("20301234563");
    });
    expect(result.current.onboarding.status).toBe("loading");

    await act(async () => {
      resolve({ externalRef: "5f2c1e10-..." });
      await promise;
    });

    expect(result.current.onboarding).toEqual({
      status: "ready",
      data: { externalRef: "5f2c1e10-..." },
    });
    expect(backend.porCuit).toHaveBeenCalledWith("20301234563");
  });

  it("porCuit no toca el estado de los otros slots", async () => {
    const backend = makeBackend();
    const { result } = renderHook(() => useArcaCredential(backend));

    await act(async () => {
      await result.current.porCuit("20301234563");
    });

    expect(result.current.onboarding.status).toBe("ready");
    expect(result.current.csr.status).toBe("idle");
    expect(result.current.credencial.status).toBe("idle");
    expect(result.current.diagnostico.status).toBe("idle");
    expect(result.current.puntosVenta.status).toBe("idle");
  });

  it("generarCsr: idle -> loading -> ready con data", async () => {
    const { promise, resolve } = deferred<{ csrPem: string; alias: string }>();
    const backend = makeBackend({ generarCsr: vi.fn().mockReturnValue(promise) });
    const { result } = renderHook(() => useArcaCredential(backend));

    act(() => {
      void result.current.generarCsr("20301234563");
    });
    expect(result.current.csr.status).toBe("loading");

    await act(async () => {
      resolve({ csrPem: "-----BEGIN...", alias: "org-1-2026" });
      await promise;
    });

    expect(result.current.csr).toEqual({
      status: "ready",
      data: { csrPem: "-----BEGIN...", alias: "org-1-2026" },
    });
  });

  it("generarCsr: un rechazo pasa a error con un Error real", async () => {
    const backend = makeBackend({
      generarCsr: vi.fn().mockRejectedValue(new Error("CUIT inválido")),
    });
    const { result } = renderHook(() => useArcaCredential(backend));

    await act(async () => {
      await result.current.generarCsr("no-es-un-cuit");
    });

    expect(result.current.csr.status).toBe("error");
    if (result.current.csr.status === "error") {
      expect(result.current.csr.error.message).toBe("CUIT inválido");
    }
  });

  it("un rechazo que no es Error (string/objeto) se normaliza a Error igual", async () => {
    const backend = makeBackend({ generarCsr: vi.fn().mockRejectedValue("boom") });
    const { result } = renderHook(() => useArcaCredential(backend));

    await act(async () => {
      await result.current.generarCsr("20301234563");
    });

    expect(result.current.csr.status).toBe("error");
    if (result.current.csr.status === "error") {
      expect(result.current.csr.error).toBeInstanceOf(Error);
      expect(result.current.csr.error.message).toBe("boom");
    }
  });

  it("completarCredencial e importarCredencial escriben al mismo slot credencial", async () => {
    const backend = makeBackend();
    const { result } = renderHook(() => useArcaCredential(backend));

    await act(async () => {
      await result.current.completarCredencial("cert-pem", 3);
    });
    expect(result.current.credencial).toEqual({
      status: "ready",
      data: { pointOfSale: 0, active: true },
    });

    await act(async () => {
      await result.current.importarCredencial("20301234563", "cert", "key");
    });
    expect(backend.importarCredencial).toHaveBeenCalledWith(
      "20301234563", "cert", "key", undefined, 0,
    );
    expect(result.current.credencial.status).toBe("ready");
  });

  it("las llamadas a un slot no tocan el estado de los otros", async () => {
    const backend = makeBackend();
    const { result } = renderHook(() => useArcaCredential(backend));

    await act(async () => {
      await result.current.generarCsr("20301234563");
    });

    expect(result.current.csr.status).toBe("ready");
    expect(result.current.onboarding.status).toBe("idle");
    expect(result.current.credencial.status).toBe("idle");
    expect(result.current.diagnostico.status).toBe("idle");
    expect(result.current.puntosVenta.status).toBe("idle");
  });

  it("reset() vuelve los 5 slots a idle", async () => {
    const backend = makeBackend();
    const { result } = renderHook(() => useArcaCredential(backend));

    await act(async () => {
      await result.current.porCuit("20301234563");
      await result.current.generarCsr("20301234563");
      await result.current.diagnosticar();
    });
    expect(result.current.onboarding.status).toBe("ready");
    expect(result.current.csr.status).toBe("ready");
    expect(result.current.diagnostico.status).toBe("ready");

    act(() => {
      result.current.reset();
    });

    expect(result.current.onboarding.status).toBe("idle");
    expect(result.current.csr.status).toBe("idle");
    expect(result.current.credencial.status).toBe("idle");
    expect(result.current.diagnostico.status).toBe("idle");
    expect(result.current.puntosVenta.status).toBe("idle");
  });

  it("una llamada vieja que resuelve tarde no pisa el resultado de una más nueva", async () => {
    const primera = deferred<{ csrPem: string; alias: string }>();
    const segunda = deferred<{ csrPem: string; alias: string }>();
    const generarCsr = vi
      .fn()
      .mockReturnValueOnce(primera.promise)
      .mockReturnValueOnce(segunda.promise);
    const backend = makeBackend({ generarCsr });
    const { result } = renderHook(() => useArcaCredential(backend));

    act(() => {
      void result.current.generarCsr("cuit-1"); // arranca primero
    });
    act(() => {
      void result.current.generarCsr("cuit-2"); // arranca segundo, todavía en vuelo el primero
    });

    // Resuelve la SEGUNDA primero (la más nueva) y después la primera (la vieja) — el
    // resultado final tiene que ser el de la segunda, no lo que resuelva último en el
    // tiempo real.
    await act(async () => {
      segunda.resolve({ csrPem: "csr-2", alias: "alias-2" });
      await segunda.promise;
    });
    expect(result.current.csr).toEqual({
      status: "ready",
      data: { csrPem: "csr-2", alias: "alias-2" },
    });

    await act(async () => {
      primera.resolve({ csrPem: "csr-1", alias: "alias-1" });
      await primera.promise;
    });
    // La resolución tardía de la primera NO debe pisar el resultado de la segunda.
    expect(result.current.csr).toEqual({
      status: "ready",
      data: { csrPem: "csr-2", alias: "alias-2" },
    });
  });

  it("reset() en medio de una llamada en vuelo: la resolución tardía no revive el slot", async () => {
    const { promise, resolve } = deferred<{ csrPem: string; alias: string }>();
    const backend = makeBackend({ generarCsr: vi.fn().mockReturnValue(promise) });
    const { result } = renderHook(() => useArcaCredential(backend));

    act(() => {
      void result.current.generarCsr("20301234563");
    });
    expect(result.current.csr.status).toBe("loading");

    act(() => {
      result.current.reset();
    });
    expect(result.current.csr.status).toBe("idle");

    await act(async () => {
      resolve({ csrPem: "csr", alias: "alias" });
      await waitFor(() => promise);
    });

    expect(result.current.csr.status).toBe("idle");
  });

  it("los callbacks devueltos son estables entre renders si el backend no cambia", () => {
    const backend = makeBackend();
    const { result, rerender } = renderHook(() => useArcaCredential(backend));
    const primeraGenerarCsr = result.current.generarCsr;

    rerender();

    expect(result.current.generarCsr).toBe(primeraGenerarCsr);
  });
});
