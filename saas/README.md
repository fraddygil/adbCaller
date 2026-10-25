# ADB Caller SaaS

Nueva aplicación multi-tenant de ADB Caller. El código legacy de la raíz se
mantiene independiente y no es una dependencia de producción del SaaS.

```text
Panel web ↔ FastAPI + PostgreSQL ↔ WSS ↔ agente ↔ ADB ↔ celulares
```

## Estado

La base operativa actual contiene:

- Estructura independiente del monorepo.
- Core ADB sin FastAPI ni SQLite.
- Protocolo estricto y versionado.
- Carpeta compilable `agent/` con ejecutor de acciones cerradas.
- Autenticación Argon2id, sesiones opacas, CSRF y roles admin/operador.
- Pairing de un uso, tokens de agente hasheados y revocación inmediata.
- WSS con negociación de protocolo, heartbeat e inventario de dispositivos.
- Cola PostgreSQL por dispositivo y journal SQLite del agente.
- Panel web dinámico con SSE, usuarios, agentes, dispositivos e historial.
- Listas JSON/CSV, `call-all` y conferencias durables en segundo plano.
- Modelo PostgreSQL multi-tenant y migraciones Alembic compatibles.
- Rol PostgreSQL runtime sin `BYPASSRLS` y aislamiento RLS validado.
- Compose para PostgreSQL, migraciones y servidor.
- Tests sin hardware.

Las acciones recorren API → PostgreSQL → WSS → agente. La primera prueba física
recomendada sigue siendo únicamente `STATUS`; las conferencias automatizadas se
validaron con agente simulado y todavía requieren certificación física en A56.

El login del tenant local ya fue comprobado. Para reanudar las pruebas, conserve
los datos existentes, abra <http://127.0.0.1:8000> y siga
`docs/manual-testing.md` empezando por `STATUS`.

## Pruebas

Desde la raíz del repositorio:

```bash
pytest -q
pytest -q -c saas/pyproject.toml saas/tests
python3 -m compileall -q saas
```

## PostgreSQL local

```bash
docker compose -f saas/compose.yaml up --build
```

El servidor queda en <http://127.0.0.1:8000>. Este compose utiliza credenciales
solo de desarrollo; producción requiere secretos externos y TLS.

## Instalación editable

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e saas/core -e saas/protocol
pip install -e saas/agent -e saas/server
```

Consulte `docs/` antes de habilitar acciones remotas.
La guía de primera ejecución está en `docs/manual-testing.md`.
