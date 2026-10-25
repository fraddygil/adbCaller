# Primera prueba manual del SaaS

La primera parte llega hasta `STATUS` sin realizar llamadas. Las secciones
finales describen las pruebas físicas supervisadas y requieren números de prueba
confirmados por el usuario.

## 1. Iniciar el servidor

Desde la raíz del repositorio:

```bash
docker compose -f saas/compose.yaml up --build -d
docker compose -f saas/compose.yaml ps -a
curl http://127.0.0.1:8000/health
```

`postgres` y `server` deben estar activos; `migrate` y `provision` deben terminar
con código 0.

## 2. Crear el primer tenant

```bash
docker compose -f saas/compose.yaml run --rm server \
  adbcaller-admin bootstrap-tenant \
  --name "Prueba" --slug prueba --email admin@example.com
```

La contraseña se solicita dos veces y no se muestra ni se guarda en el shell.
Debe tener al menos 12 caracteres.

## 3. Iniciar sesión y crear el pairing

Abra <http://127.0.0.1:8000>:

1. Inicie sesión con slug, email y contraseña.
2. Pulse **Nuevo código de pairing**.
3. Copie el código retornado. Solo se muestra una vez y caduca en 10 minutos.

## 4. Preparar el equipo Linux

```bash
sudo apt-get install android-sdk-platform-tools python3-venv
git clone URL_DEL_REPOSITORIO adbCaller
cd adbCaller/saas/agent
python3 -m venv .venv
source .venv/bin/activate
pip install -e ../core -e ../protocol -e .
adb version
adb devices -l
```

El A56 debe aparecer como `device`, no `unauthorized` u `offline`.

## 5. Emparejar el agente

Si Linux está en la misma red local que el Mac de desarrollo:

```bash
adbcaller-agent pair --server http://IP_DEL_MAC:8000 --name puesto-linux-1
adbcaller-agent run
```

Introduzca el código cuando se solicite. HTTP solo es válido para esta prueba en
una red confiable; producción requiere HTTPS/WSS.

## 6. Verificar el primer circuito

En el panel:

1. El agente debe aparecer `ONLINE`.
2. El A56 debe aparecer online y autorizado por ADB.
3. Pulse **Estado**.
4. La tarea debe terminar `DONE` con `{"state":"IDLE"}`.

`/docs` permanece disponible para diagnóstico y pruebas directas de API.

No pruebe `CALL` hasta confirmar un número no urgente y autorizado. Las pruebas
automatizadas nunca ejecutan acciones en teléfonos físicos.

## 7. Pruebas físicas supervisadas

Continúe solo con un número de prueba confirmado y nunca use números de
emergencia. Ejecute una acción cada vez y compruebe su resultado antes de pasar
a la siguiente:

1. Con el teléfono `IDLE`, pulse **Colgar** para validar el circuito sin iniciar
   una llamada.
2. Introduzca el número de prueba en **Número inicial** y pulse **Llamar**.
3. Cuando el panel muestre `ACTIVE`, pulse **Altavoz** y compruebe el audio en el
   A56.
4. Introduzca otro número autorizado en **Número adicional** y pulse **Agregar
   llamada**.
5. Cuando la segunda llamada esté `ACTIVE`, pulse **Combinar** y compruebe la
   conferencia en el teléfono.
6. Pulse **Colgar** y confirme que el estado vuelve a `IDLE`.

Con el intervalo normal, Altavoz puede tardar 1–4 s y Combinar 3–8 s después de
`ACTIVE`. No repita el clic mientras la tarea siga en ejecución.

## 8. Listas y automatización

1. Cree una lista manual o suba un CSV cuya primera columna contenga los
   números. Se aceptan como máximo 1 MB y 1000 números.
2. **Llamar en todos los dispositivos** consume un solo número pendiente por
   cada dispositivo disponible; no crea una conferencia.
3. **Iniciar conferencias en todos** crea un flujo de fondo separado por
   dispositivo y consume hasta el máximo configurado para cada conferencia.
4. Use **Cancelar** para detener una conferencia concreta o **Colgar todos los
   dispositivos** para cancelar todos los flujos y enviar `HANGUP_ALL` a cada
   agente.

Revise **Última tarea**, **Conferencias**, **Historial de llamadas** y
**Auditoría** después de cada escenario. Estas pruebas todavía requieren
validación física en el A56; su ejecución simulada no certifica One UI ni el
operador.

## Diagnóstico

```bash
docker compose -f saas/compose.yaml logs --no-color server
docker compose -f saas/compose.yaml logs --no-color migrate provision
```

En Linux, el agente registra reconexiones y errores ADB en la terminal. No
comparta el archivo de configuración: contiene el token bearer.
