---
name: mantener-sdk
description: Sincroniza arca_service_client (este repo) contra el contrato HTTP real de tixenre/arca-service -- busca cambios nuevos, verifica el shape real de request/response contra el código fuente (nunca solo contra prosa), corrige lo que haya que corregir con tests de contrato, y separa lo que es un bug del SDK (se arregla acá) de lo que es un problema del lado de arca-service (se reporta, no se parchea). Invocar cuando el usuario pida "actualizar el SDK", "revisar si arca-service cambió", "mantener el SDK al día", o algo equivalente.
---

# Mantener `arca_service_client` al día

Este skill nació de un bug real: una sesión anterior migró el SDK contra
`MIGRACION.md` solo, y el resultado no podía parsear ninguna emisión exitosa
-- la respuesta real anidaba `comprobante`/`importes`/`receptor` y
`MIGRACION.md` nunca mostró el shape completo. arca-service reaccionó bien
(agregó `API.md` + `mix arca.contrato`, un contrato generado que no se puede
desactualizar sin romper CI), pero **el mismo tipo de gap puede repetirse en
cualquier parte que ese generador todavía no cubra** -- la primera vez que
corrió este proceso completo, encontró que el REQUEST (no solo la respuesta)
tampoco coincidía con lo que el SDK mandaba, y nada en el repo lo generaba ni
lo verificaba. Este documento existe para que la próxima vuelta sea
sistemática, no una relectura a ojo.

## 0. Preparación

- `arca_service_client/` (este repo) es donde se edita. `tixenre/arca-service`
  es de solo lectura acá -- si no está clonado en la sesión, `add_repo` +
  clonar (ver las instrucciones que da esa tool). Si ya está clonado,
  `git fetch origin && git log --oneline <hash-que-tenías>..origin/main` para
  ver qué hay nuevo antes de asumir que la copia local sigue vigente -- ya
  pasó en esta sesión que hubo 14 commits nuevos sin que nadie los pidiera.
- Recordatorio de working directory: los comandos de Python (`pytest`,
  `ruff`, `black`, `mypy`, `pip install -e ".[dev]"`) corren DESDE
  `arca_service_client/` (el subdirectorio con `pyproject.toml`), no desde la
  raíz del repo. Confirmá con `pwd` antes de correr cualquiera -- el cwd de
  la sesión no siempre persiste como se espera entre llamadas, y correr
  pytest desde el lugar equivocado da un falso "todo bien" (usa la copia
  instalada, no la que estás editando) o un falso error de import.

## 1. Qué cambió, de verdad

No alcanza con leer `MIGRACION.md`. Ese documento (y `API.md`, si ya existe
para cuando corras esto) son el resumen que un humano escribió; el código y
los tests son la fuente de verdad. En particular:

- **Una afirmación de "esto no cambió" se verifica, no se cita.** Ya pasó dos
  veces en la misma sesión: `MIGRACION.md` decía que el body de la respuesta
  "seguía exactamente igual" (nunca lo dijo explícito, simple omisión) y
  después, en su propia sección "Lo que NO cambió", que "el request... sigue
  exactamente igual" (afirmación explícita). Las dos eran falsas contra lo
  que el SDK necesitaba mandar/parsear. Ninguna mentía a propósito -- "no
  cambió EN ESTA MIGRACIÓN" y "coincide con lo que el SDK implementa hoy" son
  preguntas distintas, y solo la segunda importa acá.
- Si existe `arca_service_phx/priv/contrato/*.json` (generado por
  `mix arca.contrato`), esos archivos son ground truth para las RESPUESTAS --
  úsalos como fixture directamente, con su ruta como referencia, en vez de
  transcribirlos a mano.
- Para lo que ese generador no cubre (típicamente el REQUEST -- confirmalo
  mirando qué contratos genera antes de asumir), la fuente de verdad son los
  schemas Elixir reales:
  `arca_service_phx/lib/arca_service_phx_web/schemas/*.ex` para qué se manda,
  `arca_service_phx/lib/arca_service_phx/arca/*.ex` (`comprobante_emitido.ex`,
  `api_error.ex`, etc.) para qué se devuelve.
- Confirmá cada shape contra el test suite HTTP real
  (`arca_service_phx/test/arca_service_phx_web/controllers/*_test.exs`,
  `arca_service_phx/test/arca_service_phx/webhooks_test.exs`) -- son
  requests/responses de verdad a través del pipeline completo, no solo la
  función que arma el JSON leída en aislamiento. Si un test ahí compara byte
  a byte dos formas (ej. "el webhook es el mismo documento que la API"),
  aprovechalo: es más fuerte que cualquier inferencia propia.
- Mirá `git log` de los commits nuevos en `arca-service` (títulos y, para los
  que toquen `arca_service_phx_web` o los `schemas/`, el diff) -- separá lo
  que es feature nueva (¿el SDK debería exponerla? es una decisión de
  producto, preguntale al usuario) de lo que es simplemente el contrato
  HTTP moviéndose (eso sí se corrige acá sin preguntar, es mantenimiento).

## 2. Qué es un bug del SDK vs. qué se reporta

- **Bug del SDK**: el código de este repo manda o espera algo que el
  servidor real (confirmado por código+test, no por una lectura sola) no
  produce ni acepta. Se arregla acá, con el mismo rigor de siempre (ver
  abajo). No hace falta preguntar si arreglarlo -- es lo que este skill
  existe para hacer.
- **Algo para reportar, no para parchear**: un gap en la documentación o
  las herramientas de arca-service que hace que ESTE tipo de bug sea fácil
  de reintroducir -- ej. un ejemplo de request escrito a mano que
  `mix arca.contrato --check` no verifica, mientras que el de la respuesta
  sí. Si encontrás algo así, armá un reporte con referencias exactas
  (archivo + línea, un comando que lo reproduzca) para que el usuario se lo
  pase al dev de arca-service -- no lo asumas resuelto ni lo silencies.
  Nunca lo "arregles" editando el repo de arca-service: esta sesión no
  escribe ahí.

## 3. Corrigiendo el SDK

Mismos principios que ya rigen este repo (`PRINCIPIOS.md` en arca-service es
el mismo espíritu):

- Los cambios del servidor son rompientes y sin campo de compatibilidad a
  propósito -- el SDK los sigue, sin shims de compatibilidad hacia atrás.
  Nadie está integrado en producción todavía.
- Castellano en comentarios y mensajes de commit. Los comentarios explican
  el POR QUÉ (una decisión no obvia, un valor verificado contra
  producción/tests reales), nunca el QUÉ.
- Nada de código especulativo: si no hay un caso real que lo justifique
  (un test HTTP real, un ejemplo de `priv/contrato/`, un schema fuente
  confirmado), no va.
- Cada test nuevo se corre una vez contra el código VIEJO antes de
  confiarlo -- `git stash push -- <archivos-de-código-fuente>` (nunca los
  tests), correr la suite, confirmar que falla, `git stash pop`. Si no
  falla contra el bug que dice atrapar, no sirve.
- Cuando el shape de una respuesta importa, agregalo/actualizalo en
  `tests/test_contract.py` -- fixture fijo con la referencia exacta
  (archivo + línea de arca-service) de dónde salió cada valor, más un
  chequeo en vivo contra `https://arca.mancino.dev` cuando no haga falta
  credencial (el sobre de error, por ejemplo). Ese archivo es justamente
  para que la PRÓXIMA vez que esto se desalinee, algo lo note solo.
- Bumpeá `__version__` en `arca_service_client/__init__.py` Y `version` en
  `pyproject.toml` juntos si cambió `__all__` o el shape de alguna clase
  pública -- son dos lugares, tienen que quedar sincronizados
  (`test_version.py` lo verifica).
- Verificación final, desde `arca_service_client/`: `pytest -q`,
  `ruff check .`, `black --check .`, `mypy arca_service_client`. Los cuatro
  limpios antes de considerar terminado.
- Actualizá el README (`arca_service_client/README.md`) si cambió algo que
  un ejemplo de uso muestra -- un atributo, un método nuevo, la sección de
  Errores.

## 4. Al terminar

Commit + push a la branch del SDK que la sesión tenga asignada (nunca a
`main` directo salvo que te lo pidan). Si encontraste algo para reportar
(sección 2), no lo mezcles en el commit -- es texto para el usuario, no
código.
