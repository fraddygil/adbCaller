# Seguridad

Reglas no negociables:

- TLS/WSS obligatorio fuera de desarrollo local.
- Sesiones web opacas, cookies seguras y CSRF.
- Contraseñas con Argon2id.
- Tokens de agente hasheados, rotables y revocables.
- Acciones del agente mediante enum cerrada.
- Nunca aceptar shell o ADB arbitrario desde la red.
- Validar tenant en aplicación y PostgreSQL.
- Límites de dispositivos, llamadas y comandos.
- Números fuera de logs generales.
- No guardar ni transmitir dumps reales completos de UI Automator.
- El agente se ejecuta sin privilegios administrativos.

El cliente controla físicamente el PC y puede analizar o modificar el agente.
Por ello, el servidor no confía en el agente para autorización, tenant, límites
ni decisiones comerciales.
