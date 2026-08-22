import { describe, expect, it } from "vitest";

import { cuitValido, formatearCuit, normalizarCuit } from "./cuit";

const CUIT_VALIDO = "20301234563"; // verificado contra el algoritmo mod-11

describe("normalizarCuit", () => {
  it("sin guiones pasa tal cual", () => {
    expect(normalizarCuit(CUIT_VALIDO)).toBe(CUIT_VALIDO);
  });

  it("con guiones normaliza igual", () => {
    expect(normalizarCuit("20-30123456-3")).toBe(CUIT_VALIDO);
  });

  it("con espacios normaliza igual", () => {
    expect(normalizarCuit(" 20 30123456 3 ")).toBe(CUIT_VALIDO);
  });

  it("acepta number", () => {
    expect(normalizarCuit(20301234563)).toBe(CUIT_VALIDO);
  });

  it("null/undefined dan null", () => {
    expect(normalizarCuit(null)).toBeNull();
    expect(normalizarCuit(undefined)).toBeNull();
  });

  it("vacío da null", () => {
    expect(normalizarCuit("")).toBeNull();
  });

  it("largo incorrecto da null", () => {
    expect(normalizarCuit("123")).toBeNull();
    expect(normalizarCuit("203012345631234")).toBeNull();
  });
});

describe("cuitValido", () => {
  it("un CUIT real con dígito verificador correcto es válido", () => {
    expect(cuitValido(CUIT_VALIDO)).toBe(true);
  });

  it("con guiones da el mismo resultado que sin guiones", () => {
    expect(cuitValido("20-30123456-3")).toBe(cuitValido(CUIT_VALIDO));
    expect(cuitValido(CUIT_VALIDO)).toBe(true);
  });

  it("dígito verificador incorrecto es inválido", () => {
    expect(cuitValido("20301234560")).toBe(false);
  });

  it("malformado da false, no explota", () => {
    expect(cuitValido("no es un cuit")).toBe(false);
    expect(cuitValido(null)).toBe(false);
    expect(cuitValido(undefined)).toBe(false);
  });
});

describe("formatearCuit", () => {
  it("formatea con guiones", () => {
    expect(formatearCuit(CUIT_VALIDO)).toBe("20-30123456-3");
  });

  it("acepta entrada ya formateada, ida y vuelta", () => {
    expect(formatearCuit("20-30123456-3")).toBe("20-30123456-3");
  });

  it("acepta number", () => {
    expect(formatearCuit(20301234563)).toBe("20-30123456-3");
  });

  it("malformado lanza", () => {
    expect(() => formatearCuit("123")).toThrow();
  });
});
