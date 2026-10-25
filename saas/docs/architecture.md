# Arquitectura

```text
Navegador
   │ HTTPS + SSE
   ▼
Servidor FastAPI ── PostgreSQL
   │ WSS iniciado por el agente
   ▼
Agente mínimo
   │ adb -s SERIAL
   ▼
Celulares Android
```

## Límites de confianza

- El servidor conserva usuarios, tenants, listas, workflows, límites y auditoría.
- El agente solo ejecuta acciones atómicas incluidas en una lista blanca.
- El servidor nunca recibe audio, imágenes ni jerarquías completas de UI Automator.
- El agente nunca recibe SQL, shell ni comandos ADB arbitrarios.
- Una modificación del agente solo debe poder afectar al tenant, agente y hardware
  asociados a su propio token.

## Separación del legacy

El SaaS no importa módulos desde la raíz del repositorio. `saas/core` parte del
comportamiento ADB validado, pero evoluciona como paquete independiente. Las dos
suites de pruebas deben permanecer verdes.
