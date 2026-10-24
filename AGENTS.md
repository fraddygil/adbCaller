# Instrucciones del proyecto

## Objetivo y alcance actual

Este proyecto controla uno o varios Samsung Galaxy A56 por USB mediante ADB para
iniciar, observar, contestar y finalizar llamadas.

- Las Fases 1 y 2 están implementadas. La Fase 3 fue autorizada y se desarrolla
  por pasos. Ya están validadas llamadas adicionales, combinación manual y
  automática, y Altavoz manual y automático.
- El botón de combinación fue capturado como
  `com.samsung.android.incallui:id/merge_button`. Usar ese identificador y texto
  accesible como respaldo; nunca fijar coordenadas de pantalla.
- No exigir `ACTIVE + HOLDING` para combinaciones posteriores: Telecom puede
  representar una conferencia como una llamada padre. Requerir una llamada no
  `IDLE` y confiar en que `merge_button` esté habilitado y sea pulsable.
- El altavoz automático usa el control accesible etiquetado `Speaker`/`Altavoz`
  (`com.samsung.android.incallui:id/fourth_button` en el A56 validado). Comprobar
  primero `Current route`; nunca pulsar si ya es `TYPE_SPEAKER`.
- Solo `add-call` debe marcar una combinación automática pendiente. Cuando esa
  llamada llegue a `ACTIVE`, reintentar el botón hasta cinco ciclos; la primera
  llamada nunca debe disparar auto-combinación. Mantener los controles manuales
  de Combinar y Altavoz como respaldo.
- La conferencia desde lista debe ejecutarse como tarea de fondo por dispositivo:
  nunca mantener una petición HTTP abierta durante llamadas. Consumir como
  máximo `MAX_CONFERENCE_CALLS`, esperar `ACTIVE` y confirmar cada incremento de
  combinación antes de avanzar. **Colgar** debe cancelar la tarea pendiente.
- No confundir los controles globales: `call-all` significa una llamada por
  dispositivo; `conference-all` inicia el flujo completo con los números
  pendientes de cada dispositivo. Mantener etiquetas distintas y explícitas en
  HTMX.
- La llamada inicial sigue separada de `add-call`, disponible en CLI, API y UI.
- `CallController.on_call_active()` ejecuta Altavoz automático. No añadir allí
  operaciones que puedan volver a pulsar controles ya activos.

## Stack y convenciones

- Python 3.11 o superior, FastAPI, `asyncio` y SQLite.
- Evitar dependencias nuevas cuando la biblioteca estándar resuelva el caso.
- Usar nombres de código en inglés.
- Escribir comentarios, documentación, mensajes de usuario y README en español.
- Mantener funciones pequeñas, tipos explícitos y operaciones bloqueantes fuera
  del event loop mediante `asyncio.to_thread` cuando corresponda.
- No introducir capas, configuraciones o abstracciones que no sean necesarias
  para el requisito actual.

## Arquitectura

- `adb_client.py`: único acceso a procesos ADB. Usa
  `asyncio.create_subprocess_exec`, timeout y errores explícitos.
- `call_controller.py`: validación de números, acciones telefónicas, lectura de
  estados y ciclo de vida de llamadas.
- `device_manager.py`: descubrimiento periódico, un worker y una cola por serial,
  reparto de números y eventos en vivo.
- `db.py`: esquema y acceso SQLite.
- `models.py`: enums, snapshots y modelos de entrada de la API.
- `api.py`: FastAPI, SSE y respuestas HTML para HTMX.
- `cli.py`: diagnóstico y control básico desde terminal.
- `config.py`: variables de entorno y logs separados por serial.
- `tests/fake_adb.py`: sustituto de ADB para todas las pruebas automatizadas.

Flujo principal:

```text
CLI/API/UI → DeviceManager → worker por serial → CallController → AdbClient → USB
                                      └──── SQLite + log por dispositivo
```

## Reglas ADB y telefonía

- Toda orden dirigida a un celular debe incluir `adb -s SERIAL`.
- `adb devices -l` es la única excepción porque descubre los seriales.
- Nunca construir órdenes con `shell=True` ni concatenar entrada del usuario en
  una cadena de shell.
- Conservar timeout y propagación de `AdbError` en todas las órdenes.
- Antes de acciones telefónicas, despertar el equipo con `KEYCODE_WAKEUP` y
  mantener la pantalla activa por USB con `svc power stayon usb`.
- Consultar primero `dumpsys telecom`, pero analizar exclusivamente el bloque
  actual `CallsManager.mCalls`. El resto del dump contiene historiales de
  llamadas y de rutas de audio que pueden conservar estados antiguos de la app
  Teléfono, Teams u otros servicios y producir falsos `RINGING`/`ACTIVE`.
- Usar `mCallState` de `dumpsys telephony.registry` como respaldo. Si aparecen
  varios registros, priorizar una llamada real sobre los valores `IDLE`.
- No afirmar que un extra no documentado selecciona SIM 1. `ACTION_CALL` depende
  de que One UI tenga SIM 1 como SIM predeterminada y «Preguntar siempre» esté
  desactivado.
- Los IDs de suscripción son dinámicos: nunca codificar valores observados como
  `subId`. Para comprobar SIM 1, comparar `multi_sim_voice_call` con el `subId`
  asociado a `phoneId=0` en el dispositivo actual.
- No ejecutar llamadas reales, contestar ni colgar dispositivos físicos durante
  pruebas automatizadas. Para tocar hardware real se requiere petición explícita
  del usuario y un número de prueba confirmado por él.
- No llamar números de emergencia.

## Concurrencia y errores

- Las acciones de un mismo serial deben conservar su orden mediante su worker.
- Dispositivos diferentes deben trabajar en paralelo.
- Un error o desconexión no debe cancelar acciones de otros dispositivos.
- La acción normal `call` requiere `IDLE`; únicamente `add-call` puede iniciar
  otra llamada y requiere una llamada/conferencia `ACTIVE`.
- Una desconexión debe cerrar la llamada abierta en SQLite con un resultado que
  explique la desconexión.
- Los consumidores SSE lentos no deben bloquear el polling ni los workers.

## Tiempos de automatización

- `POLL_INTERVAL` es 2.5 segundos por defecto. Un cambio de estado puede tardar
  entre 0 y un intervalo completo en ser observado.
- Al detectar `ACTIVE`, Altavoz se procesa antes de auto-combinar. Cada acción
  ejecuta `dumpsys`, `uiautomator dump`, lectura XML, limpieza y `input tap`, por
  lo que no debe esperarse respuesta instantánea.
- Con el valor predeterminado, documentar como referencia 1–4 s para Altavoz y
  3–8 s para Combinar después de `ACTIVE`. Un reintento suma al menos otro ciclo.
- Los logs deben conservar las duraciones medidas con `time.monotonic()` sin
  registrar información adicional de contactos.
- `POLL_INTERVAL=1` puede usarse con uno o dos equipos para reducir latencia. No
  bajar el valor por defecto sin probar carga ADB y estabilidad del hub con
  varios celulares.

## API y persistencia

- Preservar los endpoints públicos documentados en `README.md`.
- `GET /health` es una comprobación de vida del proceso: responde `status=ok` e
  informa `available_devices`; cero dispositivos no convierte la respuesta en
  error.
- `/v1/models` no pertenece a esta aplicación. No crear endpoints ficticios
  para satisfacer sondeos de clientes ajenos; una ruta no registrada debe
  conservar el `404` estándar de FastAPI.
- Validar números y archivos en el límite de entrada; el CSV admite como máximo
  1 MB y una carga contiene como máximo 1000 números.
- Los números directos aceptan entre 3 y 20 dígitos con `+` inicial opcional. La
  UI debe eliminar letras al escribir y el backend siempre debe volver a
  validar, aunque el navegador ya haya validado.
- Mantener SQLite compatible con instalaciones existentes al modificar tablas;
  agregar una migración si cambia el esquema después de una publicación.
- No registrar secretos. Los números telefónicos sí forman parte del historial
  solicitado, por lo que los logs y la base deben permanecer locales.
- El servidor no tiene autenticación: mantener `127.0.0.1` como host
  predeterminado y advertir antes de exponerlo en red.

## UI dinámica

- Las tarjetas se reemplazan por polling HTMX y eventos SSE. Todo control cuyo
  valor escriba el usuario debe tener un `id` estable y `hx-preserve`; de lo
  contrario perderá valor y foco durante un refresco.
- Conservar la actualización periódica de `/ui/devices` y el selector de
  dispositivos. Varias respuestas `200` de esos endpoints en el access log son
  comportamiento normal.
- La validación visual nunca sustituye la validación en `CallController` o en el
  límite de la API.

## Compatibilidad multimarcas futura

Samsung es el primer dispositivo validado, pero la arquitectura futura debe
permitir Google/Pixel, Xiaomi, Motorola y otros fabricantes sin llenar
`CallController` de condiciones por marca.

- Antes de agregar una segunda marca, extraer las acciones de interfaz a un
  contrato `InCallUiDriver` con operaciones semánticas como `merge_calls()`,
  `enable_speaker()`, `add_call()` y `end_call()`.
- Implementar perfiles separados (`SamsungUiProfile`, `GoogleUiProfile`, etc.) y
  un `GenericUiProfile` limitado. `CallController` debe pedir una acción; no debe
  conocer paquetes ni IDs específicos del fabricante.
- Seleccionar el perfil usando `ro.product.manufacturer`, modelo, versión Android,
  versión de la capa del fabricante y, cuando sea necesario, build fingerprint.
- Resolver controles en este orden: `resource-id` validado, descripción
  accesible, texto localizado y, por último, declarar la acción no soportada.
  Nunca usar coordenadas fijas como selector persistente.
- Verificar el resultado después de cada pulsación con una fuente independiente:
  `dumpsys telecom` para llamadas/conferencia y `Current route` para Altavoz.
- Los selectores pueden cambiar tras una actualización. Asociar perfiles a la
  versión observada, registrar errores claros e invalidar cualquier caché cuando
  cambie el build.

### Inspección con UI Automator

- `adb shell uiautomator dump` es una lectura estructurada de la ventana actual,
  no una captura de pantalla ni una acción. El XML puede incluir `text`,
  `content-desc`, `resource-id`, clase, estados y `bounds`.
- El flujo actual escribe un XML temporal en el teléfono, lo lee, lo analiza y
  lo elimina siempre mediante `finally`. Mantener esa limpieza en cada perfil.
- El XML puede contener nombres y números. Nunca guardar dumps reales completos,
  imprimirlos en logs ni añadirlos como fixtures sin sanitización.
- Crear en el futuro `python cli.py inspect-ui SERIAL`: debe ser solo lectura por
  defecto, mostrar únicamente controles relevantes/pulsables y ocultar datos
  personales. Cualquier modo que pulse controles debe requerir autorización
  explícita.
- Para certificar una marca: capturar llamada activa, Altavoz, llamada en espera,
  botón Combinar y conferencia; verificar cada acción físicamente y guardar un
  fixture XML mínimo y sanitizado para tests.
- `uiautomator dump` puede fallar con pantalla bloqueada, vistas gráficas
  personalizadas, WebView o superficies seguras. Marcar la capacidad como no
  soportada en vez de adivinar una posición.
- Continuar con el comando integrado mientras basten pocas acciones. Evaluar la
  dependencia `uiautomator2` solo cuando hagan falta esperas/selectores complejos
  o muchas interacciones repetidas y su beneficio justifique instalar un agente
  adicional en cada teléfono.

### Orden recomendado para incorporar otra marca

1. Implementar `InCallUiDriver` y mover el comportamiento Samsung sin cambiarlo.
2. Crear el inspector seguro y fixtures sanitizados.
3. Detectar capacidades por dispositivo (`speaker`, `add_call`, `merge`).
4. Añadir un perfil para una sola marca/modelo y versión comprobados.
5. Ejecutar los mismos escenarios físicos usados con el A56.
6. Solo después habilitar ese perfil en producción y documentar sus límites.

## Hardware validado

Entre el 20 y el 24 de octubre de 2026 se comprobó:

- Samsung Galaxy A56 `SM-A566E` por USB, con Android 16, API 36 y valor One UI
  `80500`.
- Detección ADB autorizada, `GET /devices`, `GET /health`, estado `IDLE`,
  `KEYCODE_WAKEUP` y `svc power stayon usb` (`stay_on_while_plugged_in=2`).
- La suscripción de voz predeterminada observada estaba asociada a `phoneId=0`
  (SIM 1). No conservar el `subId` observado como configuración permanente.
- Llamadas reales, segunda y posteriores llamadas mediante `ACTION_CALL`, estados
  `ACTIVE`/`HOLDING`, combinación manual y automática, y activación de Altavoz.

Siguen pendientes la prueba física con dos Galaxy controlados simultáneamente,
el límite máximo del operador y la secuencia completa a partir de una lista sin
intervención humana.

## Comandos de trabajo

Instalación:

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Verificación obligatoria después de cambios:

```bash
pytest -q
python -m compileall -q .
```

Servidor y CLI:

```bash
python api.py
python cli.py devices
python cli.py status SERIAL
```

No usar `python cli.py call`, `answer` o `hangup` como prueba automática.

## Pruebas

- Toda rama nueva no trivial debe tener una prueba pequeña.
- Usar `FakeAdb`; las pruebas deben funcionar sin ADB instalado ni celulares.
- Cubrir al menos parsing de ADB/dumpsys, timeout o errores, conexión y
  desconexión, orden de la cola y aislamiento de fallos entre celulares.
- Mantener una regresión que demuestre que eventos históricos de
  `dumpsys telecom` no alteran el estado actual.
- Al modificar la UI dinámica, comprobar que los campos editables conservan
  valor/foco y que los números con letras son rechazados.
- Usar rutas temporales para SQLite y logs; no dejar artefactos en el proyecto.
- No depender del orden o velocidad real del polling: invocar `refresh_once()`
  cuando una prueba necesite una actualización determinista.

## Archivos generados

No versionar ni editar como fuente:

- `.venv/`
- `__pycache__/` y `.pytest_cache/`
- `adb_caller.db`, archivos WAL/SHM y otras bases de ejecución
- `logs/`

## Criterio de finalización

Antes de dar un cambio por terminado:

1. Confirmar que no se avanzó de fase sin autorización.
2. Ejecutar tests y compilación.
3. Actualizar `README.md` si cambian instalación, API, límites o pruebas manuales.
4. Informar claramente qué se verificó con `FakeAdb` y qué requiere un A56 real.
5. No asegurar compatibilidad de One UI o SIM 1 sin una comprobación física.
6. No declarar compatible una nueva marca sin perfil, fixtures sanitizados y
   validación física de sus controles de llamada.
