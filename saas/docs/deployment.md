# Despliegue

El servidor se despliega con FastAPI, PostgreSQL y un proxy TLS. `compose.yaml`
incluye PostgreSQL, un paso separado de Alembic y el servidor para desarrollo.
No debe publicarse directamente: las credenciales del compose son locales y
falta agregar el proxy TLS de producción.

El agente se distribuye como ejecutable nativo construido desde `saas/agent`.
Windows y Linux requieren builds independientes. El agente inicia WSS hacia
`PUBLIC_URL`, no abre puertos y no recibe credenciales de PostgreSQL.

`.env` nunca se versiona. `.env.example` contiene solo nombres y valores de
desarrollo no secretos.
