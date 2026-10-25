# Modelo de datos previsto

Todas las tablas propiedad de un cliente tendrán `tenant_id NOT NULL`. `tenants`
es la única excepción porque representa al propio tenant.

```text
tenants
├── users ── user_sessions
├── pairing_codes
├── agents
│   ├── agent_tokens
│   ├── devices ── calls
│   └── agent_events
├── number_lists ── numbers
├── tasks
├── conference_sessions ── conference_participants
├── tenant_settings
├── audit_log
└── rate_limit_buckets
```

Se usarán UUID públicos, claves compuestas con tenant, repositorios con contexto
de tenant y Row Level Security en PostgreSQL. La primera migración se añadirá en
el bloque de servidor; no se utilizará `create_all` en producción.
