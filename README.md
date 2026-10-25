# ADB Caller — llamadas y conferencias por ADB

Control de llamadas y conferencias de uno o varios celulares Android conectados
por USB. Incluye control ADB, colas independientes por dispositivo, API, eventos
SSE, UI HTMX, registro SQLite, ajustes operativos persistentes, Altavoz
automático y combinación automática de llamadas agregadas.

## Arquitectura

```text
CLI / API / UI
       │
       ▼
DeviceManager ─── polling ADB + eventos SSE + SQLite
       │
       ├── cola/worker SERIAL A ──┐
       ├── cola/worker SERIAL B ──┼── CallController ── AdbClient ── USB
       └── cola/worker SERIAL N ──┘
```

Cada celular procesa sus acciones en orden. Los workers de celulares distintos
corren en paralelo y un error no cancela a los demás. `on_call_active()` activa
Altavoz cuando una llamada conecta; las llamadas creadas con **Agregar llamada**
quedan pendientes de combinación automática.

## Requisitos

- Python 3.11 o superior.
- Android Platform Tools (`adb`) disponible en `PATH`.
- Samsung Galaxy A56 con Android 14 o superior / One UI 7 o superior.
- Cable USB de datos o hub alimentado.

### Entorno comprobado

Las pruebas se ejecutaron entre el 20 y el 24 de octubre de 2026 en un Samsung
Galaxy A56 `SM-A566E` con Android 16, API 36 y valor
`ro.build.version.oneui=80500` (One UI 8). Se validaron detección USB/ADB,
`getprop`, API, SIM 1, llamadas reales, transición `DIALING`/`ACTIVE`/`HOLDING`,
varias llamadas, combinación manual y automática, y Altavoz manual y automático.
Sigue pendiente validar el flujo completo con dos Galaxy controlados a la vez.

### Preparar el celular Samsung

1. En **Ajustes → Acerca del teléfono → Información de software**, pulse siete
   veces **Número de compilación**.
2. En **Opciones de desarrollador**, active **Depuración USB**.
3. Conecte el teléfono desbloqueado y acepte la huella RSA; marque permitir
   siempre desde este equipo.
4. En **Ajustes → Conexiones → Administrador de SIM → Llamadas**, seleccione
   explícitamente **SIM 1**. No deje «Preguntar siempre».
5. Si existe **Bloqueador automático → Bloqueo máximo**, desactívelo o permita
   los comandos USB necesarios durante la prueba.

En Windows puede ser necesario instalar el Samsung Android USB Driver. En
Linux, configure una regla `udev` para Samsung y agregue su usuario al grupo que
pueda acceder al dispositivo. macOS normalmente no necesita driver.

## Instalación

```bash
python3.11 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
adb version
adb devices -l
```

Todos los dispositivos deben aparecer como `device`. Si aparece
`unauthorized`, desbloquee el celular y acepte la autorización. Si aparece
`offline`, reconecte el cable y ejecute `adb kill-server` seguido de
`adb start-server`.

Las variables de infraestructura se leen al iniciar:

| Variable | Predeterminado | Uso |
|---|---:|---|
| `ADB_PATH` | `adb` | Ruta al ejecutable ADB |
| `ADB_TIMEOUT` | `15` | Timeout por orden, segundos |
| `DATABASE_PATH` | `./adb_caller.db` | Base SQLite |
| `LOG_DIR` | `./logs` | Un log por serial |
| `HOST` / `PORT` | `127.0.0.1` / `8000` | Servidor web |

Los ajustes operativos se editan en el panel y se guardan en SQLite:

| Variable opcional de bloqueo | Predeterminado | Uso |
|---|---:|---|
| `POLL_INTERVAL` | `2.5` | Sondeo de dispositivos y llamadas |
| `AUTO_SPEAKER` | `true` | Activa Altavoz cuando una llamada pasa a `ACTIVE` |
| `AUTO_MERGE` | `true` | Combina una llamada agregada cuando pasa a `ACTIVE` |
| `CONFERENCE_CALL_TIMEOUT` | `15` | Espera máxima para que conteste cada número |
| `CONFERENCE_MERGE_TIMEOUT` | `30` | Espera máxima para combinar cada llamada |
| `MAX_CONFERENCE_CALLS` | `4` | Números consumidos por conferencia y dispositivo |
| `MERGE_ATTEMPTS` | `5` | Intentos de combinación automática |
| `SKIP_FAILED_NUMBERS` | `true` | Omite fallidos y continúa con el siguiente |

La precedencia es valor predeterminado, SQLite y, por último, variable de
entorno explícita. Si se define una de las variables operativas, el campo
correspondiente aparece bloqueado en el panel. Los límites validados son:
timeout de respuesta 5–120 s, timeout de combinación 5–60 s, máximo 2–4
números, 1–10 intentos e intervalo de sondeo 0.5–10 s.

## CLI: probar un celular

```bash
python cli.py devices
python cli.py call SERIAL 809XXXXXXX
python cli.py status SERIAL
python cli.py hangup SERIAL
```

También están disponibles:

```bash
python cli.py answer SERIAL
python cli.py calls SERIAL
python cli.py add-call SERIAL NUMERO
python cli.py merge SERIAL
python cli.py speaker SERIAL
```

La orden de llamada usa:

```text
adb -s SERIAL shell am start -a android.intent.action.CALL -d tel:NUMERO
```

Antes de cada acción se envía `KEYCODE_WAKEUP` y se configura
`svc power stayon usb`.

El servidor activa automáticamente **Altavoz** cuando detecta una transición a
`ACTIVE`. Primero verifica que la ruta no sea ya `TYPE_SPEAKER` y después busca
el control accesible de One UI etiquetado `Speaker`/`Altavoz`; no utiliza
coordenadas fijas. Puede deshabilitarse desde el panel. Definir
`AUTO_SPEAKER=false` lo deshabilita y bloquea ese campo.

### Agregar y combinar llamadas

Samsung Telecom acepta otro `ACTION_CALL` mientras existe una llamada activa.
La primera queda en espera y la nueva se marca por SIM 1:

```bash
python cli.py call SERIAL PRIMER_NUMERO
# Espere hasta que la primera llamada esté contestada.
python cli.py status SERIAL
python cli.py add-call SERIAL SEGUNDO_NUMERO
python cli.py calls SERIAL
```

`add-call` solo funciona si ya existe una llamada `ACTIVE`. Antes de combinar,
el resultado normal es una llamada `HOLDING` y otra `ACTIVE`. Desde CLI debe
ejecutarse `merge` explícitamente; el servidor web combina automáticamente.
Realice llamadas únicamente a números autorizados.

La misma prueba está disponible en la web:

1. Use **Llamar** para el primer número y espere que sea contestado.
2. Escriba el segundo número y pulse **Agregar llamada**.
3. Conteste el segundo teléfono y pulse **Ver llamadas**.
4. Confirme `HOLDING` y `ACTIVE`. De forma predeterminada se combinarán
   automáticamente; el botón web **Combinar** permanece como respaldo manual.

API equivalente:

```bash
curl -X POST http://127.0.0.1:8000/devices/SERIAL/add-call \
  -H 'Content-Type: application/json' -d '{"number":"8095550102"}'
curl http://127.0.0.1:8000/devices/SERIAL/calls
curl -X POST http://127.0.0.1:8000/devices/SERIAL/merge
curl -X POST http://127.0.0.1:8000/devices/SERIAL/speaker
```

El A56 validado expone el botón como
`com.samsung.android.incallui:id/merge_button`. La automatización busca primero
ese identificador accesible y solo usa el texto como respaldo; no depende de
coordenadas fijas. Después de la primera combinación, Telecom puede representar
la conferencia como una única llamada padre, por lo que las combinaciones
siguientes se autorizan cuando One UI expone ese botón habilitado, en vez de
exigir siempre el par de estados `ACTIVE + HOLDING`.

Cuando `AUTO_MERGE=true`, cada llamada iniciada con **Agregar llamada** queda
marcada para combinación. Al pasar de `DIALING` a `ACTIVE`, el manager busca el
botón de One UI y reintenta hasta cinco ciclos de polling. La llamada inicial no
activa este flujo. **Combinar** continúa disponible para uso manual.

### Conferencia automática desde una lista

1. Pegue o suba los números.
2. Seleccione un celular específico y pulse **Asignar**.
3. Compruebe el contador **Pendientes** en la tarjeta correspondiente.
4. Pulse el botón global **Iniciar conferencias con pendientes**.

El botón global **Llamar 1 por celular** conserva el comportamiento de Fase 2:
consume solo un número pendiente de cada dispositivo. No crea una conferencia.

El endpoint inicia una tarea en segundo plano y responde inmediatamente:

```bash
curl -X POST http://127.0.0.1:8000/conference-all
```

El flujo consume hasta el máximo configurado de números pendientes: llama al
primero, espera `ACTIVE`, agrega el siguiente, espera que conecte y que se
combine, y continúa. La tarjeta muestra `conference_state`, progreso y error.
Los números que excedan el máximo permanecen pendientes. **Colgar** cancela la
tarea antes de enviar `KEYCODE_ENDCALL`, por lo que no se marca el siguiente
número. **Colgar todos** aplica la misma cancelación a cada dispositivo.

Si un número no contesta durante el timeout, rechaza o falla, se termina ese
intento, se registra el resultado y se continúa con el siguiente cuando
**Omitir fallidos y continuar** está activo. La aplicación no vuelve a marcar
automáticamente un intento cuyo estado quedó incierto.

Estados posibles: `STARTING`, `WAITING_ACTIVE`, `ADDING`, `MERGING`, `ACTIVE`,
`FAILED`, `CANCELLED`, `ENDED`, `WAITING_DEVICE` y `RECOVERED_PAUSED`.

### Recuperación después de reiniciar o desconectar

La cola, la sesión y el estado de cada participante se guardan en SQLite. Un
reinicio no reanuda llamadas sin supervisión: la sesión aparece como
`RECOVERED_PAUSED` y ofrece **Reanudar conferencia** y **Cancelar sesión
recuperada**. Una desconexión usa `WAITING_DEVICE` y pasa a pausa al reconectar.

Antes de reanudar, compruebe físicamente la pantalla del teléfono. Las acciones
que ya estaban en `DIALING` o `ACTIVE` cuando se interrumpió el proceso no se
repiten; solo continúan los participantes que seguían en `PENDING`.

### Tiempos de Altavoz y combinación

Las acciones no se ejecutan en el instante exacto en que cambia la pantalla. El
servidor observa el teléfono mediante polling y después usa varias órdenes ADB:

```text
La llamada conecta
  → esperar siguiente polling (0–2.5 s con la configuración predeterminada)
  → dumpsys telecom
  → comprobar/activar Altavoz con uiautomator
  → volver a validar llamadas
  → generar otra jerarquía uiautomator
  → pulsar Combinar
```

Con `POLL_INTERVAL=2.5`, los tiempos esperados después de que la llamada queda
`ACTIVE` son:

- **Altavoz:** normalmente 1–4 segundos.
- **Combinar:** normalmente 3–8 segundos, porque se procesa después del Altavoz.
- Si `merge_button` todavía no está habilitado, cada reintento añade al menos un
  ciclo de 2.5 segundos más el tiempo de `uiautomator dump`.

En las pruebas observadas, desde el envío de **Agregar llamada** —incluyendo el
tiempo de timbrado y respuesta— hasta solicitar **Combinar** transcurrieron
aproximadamente 9.5–13 segundos. Los logs ahora registran por separado el tiempo
de Altavoz, el tiempo de la orden de combinación y el tiempo desde que el
servidor detecta `ACTIVE`.

Para priorizar rapidez con uno o dos dispositivos puede probarse un intervalo
de 1 segundo desde el panel. También puede fijarse con
`POLL_INTERVAL=1 python3 api.py`; en ese caso quedará bloqueado en la UI.

Un intervalo menor aumenta la cantidad de procesos ADB y la carga del hub/PC.
Mantenga `2.5` al controlar muchos dispositivos o si aparecen estados `offline`.
El refresco HTMX de las tarjetas no causa esta demora; la automatización ocurre
en el backend.

## Servidor, UI y dos celulares

```bash
python api.py
# o: uvicorn api:app --host 127.0.0.1 --port 8000
```

Abra <http://127.0.0.1:8000>. Para probar dos teléfonos:

1. Conecte ambos y confirme que `adb devices -l` muestra dos seriales `device`.
2. Compruebe que aparecen dos tarjetas en la UI.
3. Pegue al menos dos números, elija reparto automático y pulse **Asignar**.
4. Pulse **Llamar a todos**: se consume un número pendiente por celular.
5. Verifique estados independientes y pulse **Colgar todos**.
6. Desconecte uno: en unos 2–3 segundos debe verse como desconectado, sin
   interrumpir el otro.

Los números adicionales permanecen en la cola SQLite. Cada nueva pulsación de
**Llamar a todos** consume el siguiente de cada celular. Reiniciar el servidor
conserva esa cola y pausa cualquier conferencia que estuviera en curso.

Los campos de llamada aceptan únicamente entre 3 y 20 dígitos, con `+` opcional
al inicio. Conservan su texto y foco durante las actualizaciones HTMX de las
tarjetas.

### API

```bash
curl http://127.0.0.1:8000/devices
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/settings

curl -X POST http://127.0.0.1:8000/devices/SERIAL/call \
  -H 'Content-Type: application/json' -d '{"number":"8095550101"}'

curl -X POST http://127.0.0.1:8000/call-all \
  -H 'Content-Type: application/json' \
  -d '{"assignments":{"SERIAL_A":"8095550101","SERIAL_B":"8095550102"}}'

curl -X POST http://127.0.0.1:8000/hangup-all

curl -X PATCH http://127.0.0.1:8000/settings \
  -H 'Content-Type: application/json' \
  -d '{"conference_call_timeout":20,"auto_speaker":true}'

curl -X POST http://127.0.0.1:8000/devices/SERIAL/conference/resume
curl -X POST http://127.0.0.1:8000/devices/SERIAL/conference/cancel

curl -F 'numbers=8095550101
8095550102' -F 'target_serial=' http://127.0.0.1:8000/numbers
```

`GET /events` entrega eventos SSE. La documentación interactiva está en
<http://127.0.0.1:8000/docs>.

`GET /health` responde `200 OK` mientras el proceso está operativo e informa
cuántos dispositivos ADB están disponibles. Un resultado con cero dispositivos
sigue siendo una respuesta válida del proceso:

```json
{"status":"ok","available_devices":0}
```

## Estado y datos

- Del resultado de `dumpsys telecom` se analiza únicamente
  `CallsManager.mCalls`, que distingue `DIALING`, `RINGING`, `ACTIVE` y
  `HOLDING`. Los historiales del mismo dump se ignoran porque conservan estados
  antiguos y no representan una llamada actual.
- `dumpsys telephony.registry` (`mCallState`) se usa como respaldo.
- `calls` guarda serial, número, inicio, fin, resultado y error.
- `devices` guarda el último modelo, estado ADB y versión Android observados.
- `app_settings` y `settings_audit` guardan ajustes y su historial de cambios.
- `number_queue`, `conference_sessions` y `conference_participants` permiten
  recuperar colas y sesiones sin repetir intentos inciertos.
- `logs/SERIAL.log` contiene el log independiente de cada celular.

## Pruebas automáticas

No requieren celulares: usan `FakeAdb` para simular `dumpsys`, fallos y
desconexiones.

```bash
pytest -q
```

La suite incluye regresiones para impedir que el historial de `dumpsys telecom`
simule una llamada activa, conservar campos durante refrescos HTMX, rechazar
letras en números telefónicos, persistir ajustes y colas, cancelar antes del
siguiente número, omitir números sin respuesta y recuperar sesiones pausadas.

## Logs HTTP y rutas inexistentes

- Las peticiones repetidas a `/ui/devices` y `/ui/device-options` corresponden
  al polling y a los eventos en vivo de la UI.
- Este proyecto no expone `/v1/models`. Si un cliente solicita esa ruta,
  FastAPI responde correctamente `404 Not Found`.
- El archivo `models.py` contiene modelos Python; su nombre no crea rutas HTTP.
- El access log básico muestra IP y puerto del cliente, pero no permite conocer
  retroactivamente su proceso de origen.

## Límites conocidos del Galaxy A56 / One UI

1. **SIM 1 no puede imponerse de forma portable con `ACTION_CALL`.** Android no
   ofrece por ADB un extra público y estable para elegir una SIM. Esta versión
   depende de que SIM 1 sea la SIM predeterminada para llamadas y que
   «Preguntar siempre» esté desactivado. Verifique haciendo una llamada manual
   y otra por CLI; ambas deben mostrar SIM 1 en la pantalla de llamada.
   Como diagnóstico adicional, `settings get global multi_sim_voice_call`
   devuelve el `subId` predeterminado; ese valor debe corresponder a
   `phoneId=0`. No lo codifique porque puede cambiar al reemplazar o reactivar
   una SIM.
2. **Permiso de llamada.** Algunas compilaciones u operadores pueden impedir
   que el usuario `shell` lance `ACTION_CALL`, o convertirlo en una pantalla de
   confirmación. Verifique `python cli.py call ...` con el teléfono desbloqueado
   y revise `adb logcat` si aparece `SecurityException`.
3. **Respuesta con `KEYCODE_CALL`.** One UI puede ignorarla con pantalla segura,
   políticas empresariales o versiones recientes. Verifique con una llamada
   entrante real y el teléfono bloqueado y desbloqueado.
4. **Pantalla de bloqueo.** `KEYCODE_WAKEUP` enciende la pantalla, pero no omite
   PIN, patrón ni biometría. Para pruebas supervisadas, mantenga el equipo
   desbloqueado y conectado.
5. **Permiso de `dumpsys`.** Una actualización de seguridad puede ocultar parte
   de `telecom`; el respaldo solo distingue `IDLE`, `RINGING` y ocupado
   (`ACTIVE`). Compare `python cli.py status SERIAL` con la pantalla real.
6. **Versión y modelo.** Compruebe exactamente lo detectado con:

   ```bash
   adb -s SERIAL shell getprop ro.product.manufacturer
   adb -s SERIAL shell getprop ro.product.model
   adb -s SERIAL shell getprop ro.build.version.release
   adb -s SERIAL shell getprop ro.build.version.sdk
   adb -s SERIAL shell getprop ro.build.version.oneui
   ```

   `/devices` marca `compatible=true` únicamente para Samsung con Android 14+.
7. **USB/hub.** Un hub sin alimentación suficiente provoca estados `offline` y
   desconexiones intermitentes. Pruebe cada cable directamente y luego el hub;
   observe `adb devices -l` durante varios minutos.
8. **Límite de conferencia.** Agregar y combinar varias llamadas ya fue validado
   en el A56. El límite definitivo depende del operador; el software limita
   `add-call` a cuatro llamadas actuales, pero la representación padre/hijos de
   Samsung debe seguir verificándose al alcanzar el máximo contratado.
9. **Uso responsable.** Operadores y normativas pueden limitar llamadas
   automatizadas. No llame servicios de emergencia ni números sin autorización.
10. **Red local.** No hay autenticación HTTP. El servidor escucha solo en
     `127.0.0.1` por defecto; no lo publique en una red sin agregar seguridad.

## Roadmap multimarcas

Samsung/One UI es el único perfil físicamente validado. Para incorporar Pixel,
Xiaomi, Motorola u otras marcas se separará la automatización visual mediante un
`InCallUiDriver` y perfiles por fabricante. Cada perfil resolverá acciones como
Altavoz y Combinar por `resource-id` o accesibilidad, nunca por coordenadas
persistentes, y verificará el resultado con `dumpsys telecom`.

También se prevé un comando seguro `python cli.py inspect-ui SERIAL` para
inventariar controles accesibles sin pulsarlos ni mostrar nombres o números. Los
dumps XML usados como pruebas deberán ser mínimos y sanitizados. No se declarará
compatible una marca hasta probar físicamente llamada activa, espera, Altavoz,
combinación y conferencia en una versión concreta de su aplicación Teléfono.

El proyecto seguirá usando `adb shell uiautomator dump` mientras estas pocas
acciones sean suficientes. `uiautomator2` se evaluará solo si los nuevos perfiles
necesitan esperas y selectores más complejos.

## Checklist manual

- [ ] `adb devices -l` muestra cada serial como `device`.
- [ ] `getprop` confirma Samsung, modelo A56 y Android 14+.
- [ ] SIM 1 es predeterminada y no aparece selector de SIM.
- [ ] `python cli.py call SERIAL NUMERO` inicia la llamada por SIM 1.
- [ ] `status` recorre `DIALING`/`ACTIVE` y vuelve a `IDLE`.
- [ ] `hangup` termina la llamada con pantalla encendida y apagada.
- [ ] Una llamada agregada pasa por `HOLDING + ACTIVE` y se combina sola.
- [ ] **Combinar** funciona como respaldo después de la primera conferencia.
- [ ] Altavoz se activa al conectar y el botón manual no lo desactiva si ya está activo.
- [ ] La UI muestra modelo, serial, Android, ADB y estado en vivo.
- [ ] Textarea y CSV asignan números a un serial y en reparto automático.
- [ ] Dos celulares llaman en paralelo y conservan logs separados.
- [ ] Un fallo o desconexión no detiene el otro celular.
- [ ] **Colgar todos** funciona como parada de emergencia.
- [ ] Un número sin respuesta se omite después del timeout y continúa el siguiente.
- [ ] Reiniciar durante una conferencia muestra `RECOVERED_PAUSED` sin repetir el número incierto.
- [ ] `adb_caller.db` contiene filas en `devices` y `calls` con inicio/fin.
- [ ] `pytest -q` finaliza sin errores.
