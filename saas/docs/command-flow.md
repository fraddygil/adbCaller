# Flujo de comandos

```text
Operador → API: crear acción
API → PostgreSQL: tarea QUEUED + auditoría
Dispatcher → PostgreSQL: reclamar tarea por dispositivo
Dispatcher → Agente WSS: command(command_id)
Agente → Journal SQLite: registrar antes de ejecutar
Agente → Servidor: RUNNING
Agente → Core: ejecutar acción cerrada
Core → Android: adb -s SERIAL ...
Agente → Journal: resultado
Agente → Servidor: DONE o FAILED
Servidor → PostgreSQL: resultado y auditoría
Servidor → Panel SSE: nuevo estado
```

Los reintentos de transporte reutilizan `command_id`. Una llamada que quedó en
estado incierto no vuelve a marcarse automáticamente.

Los workflows de llamada múltiple viven en el servidor y envían acciones
atómicas. Si WSS se pierde, el servidor no envía el siguiente número.

`call-all` consume como máximo un número por dispositivo. `conference-all` crea
una sesión durable independiente por dispositivo y nunca mantiene abierta la
petición HTTP durante la llamada.
