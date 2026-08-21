# @tixenre/arca-service-ui

Hook React **headless** para el onboarding de credencial de
[arca-service](https://github.com/tixenre/arca-service) — sin JSX ni CSS, el producto
pone su propia UI arriba del estado que expone este hook. Un wizard de varios pasos con
estilos propios de este paquete se nota "pegado" en una UI ajena; un bloque de estado no.

## Instalación

Publicado en GitHub Packages, bajo el scope `@tixenre`:

```
npm install @tixenre/arca-service-ui
```

(requiere tener configurado el registry de GitHub Packages para el scope `@tixenre` —
ver la [doc de npm](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-npm-registry#installing-a-package)).

## Uso

`useArcaCredential` no habla HTTP directo con arca-service — recibe un `backend` que
VOS implementás contra tu propio servidor (que a su vez le pega a arca-service,
típicamente vía [`arca_service_client`](../arca_service_client) si tu backend es
Python). Sin `external_ref` explícito en ningún método: tu backend ya sabe de qué org
se trata por la sesión del usuario.

```tsx
import { useArcaCredential, type ArcaServiceBackend } from "@tixenre/arca-service-ui";

const backend: ArcaServiceBackend = {
  generarCsr: (cuit, regenerar) =>
    fetch("/api/arca/csr", { method: "POST", body: JSON.stringify({ cuit, regenerar }) })
      .then((r) => r.json()),
  completarCredencial: (certPem, pointOfSale) =>
    fetch("/api/arca/credencial/completar", { method: "POST", body: JSON.stringify({ certPem, pointOfSale }) })
      .then((r) => r.json()),
  importarCredencial: (cuit, certPem, keyPem, keyPassword, pointOfSale) =>
    fetch("/api/arca/credencial/importar", { method: "POST", body: JSON.stringify({ cuit, certPem, keyPem, keyPassword, pointOfSale }) })
      .then((r) => r.json()),
  diagnosticar: () => fetch("/api/arca/diagnostico", { method: "POST" }).then((r) => r.json()),
  listarPuntosDeVenta: () => fetch("/api/arca/puntos-venta").then((r) => r.json()),
};

function OnboardingWizard() {
  const { csr, credencial, diagnostico, generarCsr, completarCredencial, reset } =
    useArcaCredential(backend);

  return (
    <div>
      {csr.status === "idle" && <button onClick={() => generarCsr("20301234563")}>Generar CSR</button>}
      {csr.status === "loading" && <p>Generando...</p>}
      {csr.status === "ready" && <pre>{csr.data.csrPem}</pre>}
      {csr.status === "error" && <p>Error: {csr.error.message}</p>}
      {/* ...el resto del wizard, con tu propia UI */}
    </div>
  );
}
```

## Los 4 slots

`csr` / `credencial` / `diagnostico` / `puntosVenta` — cada uno es independiente:

```ts
type AsyncSlot<T> =
  | { status: "idle" }
  | { status: "loading" }
  | { status: "ready"; data: T }
  | { status: "error"; error: Error };
```

`completarCredencial` e `importarCredencial` escriben AL MISMO slot `credencial` — son
los dos caminos hacia el mismo resultado (sin cert todavía vs. con cert propio, ver
README de `arca_service_client`).

`reset()` vuelve los 4 a `idle` — e invalida cualquier llamada todavía en vuelo, para
que su resolución tardía no reviva un slot que el usuario ya reseteó (ej. cerrar el
wizard antes de que una llamada lenta termine).

## Orden de respuestas

Si se dispara una segunda llamada al mismo slot antes de que la primera resuelva (doble
click, o cambiar de `completarCredencial` a `importarCredencial` mientras el primero
sigue en vuelo), el resultado final es siempre el de la llamada MÁS RECIENTE — una
resolución tardía de una llamada vieja nunca pisa un estado más nuevo.

## Licencia

Proprietary — uso restringido a integradores autorizados de arca-service.
