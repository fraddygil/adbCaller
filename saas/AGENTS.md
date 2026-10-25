# Instrucciones del SaaS

## Separación

- Todo el sistema SaaS vive bajo `saas/`.
- No importar módulos del legacy ubicado en la raíz.
- No modificar el comportamiento legacy para resolver necesidades del SaaS.
- `agent/` es la unidad desde la que se instala y compila el agente.

## Seguridad

- El servidor conserva autenticación, tenant, colas, workflows, límites y auditoría.
- El agente solo ejecuta acciones atómicas incluidas en `CommandAction`.
- Nunca aceptar shell, ADB arbitrario, scripts o rutas remotas.
- Toda tabla de datos de cliente debe incluir `tenant_id` y pruebas de aislamiento.
- No confiar en `tenant_id`, `agent_id`, `device_id` ni resultados aportados por el cliente.
- No registrar tokens, contraseñas, dumps UI completos ni números fuera del historial solicitado.

## Calidad

- Python 3.11 o superior, tipos explícitos y operaciones bloqueantes fuera del event loop.
- Mantener pequeños los paquetes y no añadir microservicios sin una necesidad medida.
- Ejecutar siempre:

```bash
pytest -q tests
pytest -q -c saas/pyproject.toml saas/tests
python3 -m compileall -q saas
```

- Las pruebas automatizadas usan `FakeAdb`; nunca realizan llamadas físicas.
- No hacer commit ni push sin autorización explícita del usuario.

## Estado de reanudación

- El 25 de octubre de 2026 se confirmó el login real del tenant `prueba`. No
  borrar ese tenant, sus usuarios ni cambiar contraseñas.
- Están implementados panel, SSE, pairing, roles, inventario, tareas durables,
  listas JSON/CSV, `call-all`, `conference-all`, historial, auditoría, ajustes,
  **Contestar** y **Colgar todos** con cancelación de conferencias y tareas en
  cola.
- PostgreSQL local tiene aplicada `0002_number_task`; Compose y `/health`
  quedaron operativos. La última validación completa pasó 60 pruebas.
- Los flujos WSS se probaron con agente simulado, incluido
  `CALL → Altavoz → ADD_CALL → MERGE`, `ANSWER` y `HANGUP_ALL`. Esto no sustituye
  una prueba física.
- Al reanudar, abrir el panel local y ejecutar primero solo `STATUS` con el A56.
  No realizar llamadas reales sin una nueva petición explícita y un número de
  prueba confirmado.
- Siguen pendientes por infraestructura: certificación física del A56,
  construcción del ejecutable en el Linux de destino, HTTPS/WSS y Neon con una
  credencial nueva. No reutilizar la credencial Neon que fue publicada.
- Todo `saas/` continúa sin commit. No hacer commit ni push hasta recibir
  autorización explícita.
