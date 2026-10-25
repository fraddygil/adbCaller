# Emparejamiento

```text
Admin → Servidor: generar código de un uso
Servidor → PostgreSQL: guardar hash, tenant y expiración
Servidor → Admin: mostrar código una sola vez

Agente → Servidor HTTPS: código, nombre y versión
Servidor → PostgreSQL: consumir código en una transacción
Servidor → Agente: agent_id y token opaco
Agente → almacén del SO: guardar token

Agente → Servidor WSS: Authorization Bearer
Agente → Servidor: hello y rango de protocolo
Servidor → Agente: versión elegida
Servidor → Panel: agente online
```

El token será aleatorio, revocable y se almacenará únicamente hasheado en el
servidor. Nunca se enviará en una URL.
