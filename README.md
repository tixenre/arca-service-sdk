# arca-service-sdk

Clientes oficiales para integrar [arca-service](https://github.com/tixenre/arca-service)
(facturación electrónica ARCA/AFIP) — para que cada integrador no arme su propio wrapper
HTTP sobre la misma API.

Repo público (a diferencia de `arca-service`, que sigue privado): estos paquetes son el
CONTRATO hacia afuera, no exponen ningún dato ni lógica interna de ese servicio — ni
siquiera el esquema de sellado de credenciales es secreto en sí (es cifrado de clave
pública; lo único sensible es la clave PRIVADA de cada integrador, que nunca vive acá).

## Paquetes

- **[`arca_service_client/`](arca_service_client/)** — cliente Python (mTLS + API key,
  `httpx`) + CLI (`arca-service-client login`/`whoami`, mismo patrón que `stripe login`/
  `gh auth login`: self-serve, credenciales guardadas solas, nunca un PEM a mano).
  Instalable vía
  `pip install "arca-service-client @ git+https://github.com/tixenre/arca-service-sdk.git@main#subdirectory=arca_service_client"`
  (todavía sin ningún tag publicado -- ver el README del paquete para el
  comando con tag, a preferir apenas exista uno). Ver su propio README
  para la guía completa.
- **[`arca_service_ui/`](arca_service_ui/)** — hook React headless
  (`@tixenre/arca-service-ui`) para el flujo de onboarding de credencial. Publicado a
  GitHub Packages en cada tag `arca-service-ui-vX.Y.Z`. Ver su propio README.

Cada paquete versiona y publica por separado (tags `arca-service-client-vX.Y.Z` /
`arca-service-ui-vX.Y.Z`): viven en el mismo repo por conveniencia de desarrollo, pero
son dependencias independientes para quien los consume.

## Licencia

Proprietary — repo público para que cualquier integrador de arca-service pueda leer,
instalar y auditar el código de estos clientes; no es una licencia de código abierto.
