from __future__ import annotations

import asyncio
import csv
import html
import io
import json
import re
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, StreamingResponse

from adb_client import AdbClient, AdbError
from call_controller import CallController
from config import BASE_DIR, settings
from db import Database
from device_manager import DeviceManager, DeviceUnavailableError
from models import AdbState, CallAllRequest, CallRequest, ConferenceState
from runtime_settings import RuntimeSettings


database = Database(settings.database_path)
runtime_settings = RuntimeSettings(
    database,
    {
        "poll_interval": settings.poll_interval,
        "auto_speaker": settings.auto_speaker,
        "auto_merge": settings.auto_merge,
        "conference_call_timeout": settings.conference_call_timeout,
        "conference_merge_timeout": settings.conference_merge_timeout,
        "max_conference_calls": settings.max_conference_calls,
        "merge_attempts": settings.merge_attempts,
        "skip_failed_numbers": settings.skip_failed_numbers,
    },
)
adb = AdbClient(settings.adb_path, settings.adb_timeout)
controller = CallController(adb, database, auto_speaker=settings.auto_speaker)
manager = DeviceManager(
    adb,
    controller,
    database,
    settings.poll_interval,
    auto_merge=settings.auto_merge,
    conference_call_timeout=settings.conference_call_timeout,
    conference_merge_timeout=settings.conference_merge_timeout,
    max_conference_calls=settings.max_conference_calls,
    merge_attempts=settings.merge_attempts,
    skip_failed_numbers=settings.skip_failed_numbers,
)


@asynccontextmanager
async def lifespan(_: FastAPI):
    await database.initialize()
    await runtime_settings.initialize()
    manager.apply_settings(runtime_settings.values)
    await manager.start()
    yield
    await manager.stop()


app = FastAPI(title="ADB Caller", version="0.3.0", lifespan=lifespan)


@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    content = (Path(BASE_DIR) / "templates" / "index.html").read_text(encoding="utf-8")
    return HTMLResponse(content)


@app.get("/devices")
async def get_devices():
    return [snapshot.to_dict() for snapshot in manager.snapshots()]


@app.get("/health")
async def health():
    snapshots = manager.snapshots()
    available = sum(
        snapshot.connected and snapshot.adb_state is AdbState.DEVICE
        for snapshot in snapshots
    )
    return {"status": "ok", "available_devices": available}


@app.get("/settings")
async def get_settings():
    return {"settings": runtime_settings.describe(), "recovery_policy": "pause"}


@app.patch("/settings")
async def update_settings(updates: dict[str, Any]):
    try:
        await runtime_settings.update(updates)
        manager.apply_settings(runtime_settings.values)
        return {"settings": runtime_settings.describe()}
    except Exception as exc:
        raise _http_error(exc) from exc


@app.post("/devices/{serial}/call")
async def call_device(serial: str, request: CallRequest):
    try:
        state = await manager.call(serial, request.number)
        return {"serial": serial, "state": state}
    except Exception as exc:
        raise _http_error(exc) from exc


@app.post("/devices/{serial}/hangup")
async def hangup_device(serial: str):
    try:
        state = await manager.hangup(serial)
        return {"serial": serial, "state": state}
    except Exception as exc:
        raise _http_error(exc) from exc


@app.post("/devices/{serial}/add-call")
async def add_call_device(serial: str, request: CallRequest):
    try:
        state = await manager.add_call(serial, request.number)
        return {"serial": serial, "state": state}
    except Exception as exc:
        raise _http_error(exc) from exc


@app.get("/devices/{serial}/calls")
async def get_current_calls(serial: str):
    try:
        calls = await manager.current_calls(serial)
        return [
            {"call_id": call.call_id, "state": call.state.value}
            for call in calls
        ]
    except Exception as exc:
        raise _http_error(exc) from exc


@app.post("/devices/{serial}/merge")
async def merge_device_calls(serial: str):
    try:
        await manager.merge_calls(serial)
        return {"serial": serial, "result": "MERGE_REQUESTED"}
    except Exception as exc:
        raise _http_error(exc) from exc


@app.post("/devices/{serial}/speaker")
async def enable_device_speaker(serial: str):
    try:
        changed = await manager.enable_speaker(serial)
        return {
            "serial": serial,
            "result": "SPEAKER_ENABLED" if changed else "ALREADY_ENABLED",
        }
    except Exception as exc:
        raise _http_error(exc) from exc


@app.post("/call-all")
async def call_all(request: CallAllRequest):
    try:
        return {"results": await manager.call_all(request.assignments)}
    except Exception as exc:
        raise _http_error(exc) from exc


@app.post("/hangup-all")
async def hangup_all():
    return {"results": await manager.hangup_all()}


@app.post("/conference-all")
async def start_all_conferences():
    try:
        return {"results": await manager.start_all_conferences()}
    except Exception as exc:
        raise _http_error(exc) from exc


@app.post("/devices/{serial}/conference/resume")
async def resume_device_conference(serial: str):
    try:
        return {"serial": serial, "pending": await manager.resume_conference(serial)}
    except Exception as exc:
        raise _http_error(exc) from exc


@app.post("/devices/{serial}/conference/cancel")
async def cancel_device_conference(serial: str):
    try:
        await manager.cancel_recovered_conference(serial)
        return {"serial": serial, "state": ConferenceState.CANCELLED}
    except Exception as exc:
        raise _http_error(exc) from exc


@app.get("/events")
async def events() -> StreamingResponse:
    queue = manager.subscribe()

    async def stream():
        try:
            yield "event: ready\ndata: {}\n\n"
            while True:
                try:
                    message = await asyncio.wait_for(queue.get(), timeout=15)
                    yield f"data: {json.dumps(message, ensure_ascii=False)}\n\n"
                except TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            manager.unsubscribe(queue)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/numbers")
async def upload_numbers(
    numbers: str = Form(default=""),
    target_serial: str = Form(default=""),
    csv_file: UploadFile | None = File(default=None),
):
    try:
        parsed = _parse_text_numbers(numbers)
        if csv_file and csv_file.filename:
            content = await csv_file.read(1_000_001)
            if len(content) > 1_000_000:
                raise ValueError("El CSV no puede superar 1 MB")
            parsed.extend(_parse_csv_numbers(content.decode("utf-8-sig")))
        assigned = await manager.assign_numbers(parsed, target_serial or None)
        return {"assignments": assigned}
    except (UnicodeDecodeError, csv.Error) as exc:
        raise HTTPException(400, "CSV inválido; use UTF-8") from exc
    except Exception as exc:
        raise _http_error(exc) from exc


@app.get("/ui/devices", response_class=HTMLResponse)
async def ui_devices() -> str:
    snapshots = manager.snapshots()
    if not snapshots:
        return '<p class="empty">No hay celulares detectados.</p>'
    return "".join(_device_card(snapshot) for snapshot in snapshots)


@app.get("/ui/device-options", response_class=HTMLResponse)
async def ui_device_options() -> str:
    options = ['<option value="">Repartir automáticamente</option>']
    for snapshot in manager.snapshots():
        if snapshot.connected and snapshot.adb_state is AdbState.DEVICE:
            serial = html.escape(snapshot.serial)
            options.append(f'<option value="{serial}">{serial}</option>')
    return "".join(options)


@app.post("/ui/numbers", response_class=HTMLResponse)
async def ui_upload_numbers(
    numbers: str = Form(default=""),
    target_serial: str = Form(default=""),
    csv_file: UploadFile | None = File(default=None),
) -> str:
    try:
        parsed = _parse_text_numbers(numbers)
        if csv_file and csv_file.filename:
            content = await csv_file.read(1_000_001)
            if len(content) > 1_000_000:
                raise ValueError("El CSV no puede superar 1 MB")
            parsed.extend(_parse_csv_numbers(content.decode("utf-8-sig")))
        assigned = await manager.assign_numbers(parsed, target_serial or None)
        details = ", ".join(f"{html.escape(k)}: {len(v)}" for k, v in assigned.items())
        return (
            f'<span class="success">Asignados — {details}. '
            "Pulse Iniciar conferencias con pendientes.</span>"
        )
    except Exception as exc:
        return f'<span class="error">{html.escape(str(exc))}</span>'


@app.post("/ui/devices/{serial}/call", response_class=HTMLResponse)
async def ui_call_device(serial: str, number: str = Form()) -> str:
    try:
        state = await manager.call(serial, number)
        return f'<span class="success">{state.value}</span>'
    except Exception as exc:
        return f'<span class="error">{html.escape(str(exc))}</span>'


@app.post("/ui/devices/{serial}/hangup", response_class=HTMLResponse)
async def ui_hangup_device(serial: str) -> str:
    try:
        state = await manager.hangup(serial)
        return f'<span class="success">{state.value}</span>'
    except Exception as exc:
        return f'<span class="error">{html.escape(str(exc))}</span>'


@app.post("/ui/devices/{serial}/add-call", response_class=HTMLResponse)
async def ui_add_call_device(serial: str, number: str = Form()) -> str:
    try:
        state = await manager.add_call(serial, number)
        suffix = "; se combinará al conectar" if manager.auto_merge else ""
        return f'<span class="success">Llamada agregada: {state.value}{suffix}</span>'
    except Exception as exc:
        return f'<span class="error">{html.escape(str(exc))}</span>'


@app.get("/ui/devices/{serial}/calls", response_class=HTMLResponse)
async def ui_current_calls(serial: str) -> str:
    try:
        calls = await manager.current_calls(serial)
        if not calls:
            return '<span class="empty">No hay llamadas actuales</span>'
        return " · ".join(
            f"<code>{html.escape(call.call_id)}</code>: <strong>{call.state.value}</strong>"
            for call in calls
        )
    except Exception as exc:
        return f'<span class="error">{html.escape(str(exc))}</span>'


@app.post("/ui/devices/{serial}/merge", response_class=HTMLResponse)
async def ui_merge_calls(serial: str) -> str:
    try:
        await manager.merge_calls(serial)
        return '<span class="success">Orden de combinar enviada</span>'
    except Exception as exc:
        return f'<span class="error">{html.escape(str(exc))}</span>'


@app.post("/ui/devices/{serial}/speaker", response_class=HTMLResponse)
async def ui_enable_speaker(serial: str) -> str:
    try:
        changed = await manager.enable_speaker(serial)
        message = "Altavoz activado" if changed else "El altavoz ya estaba activo"
        return f'<span class="success">{message}</span>'
    except Exception as exc:
        return f'<span class="error">{html.escape(str(exc))}</span>'


@app.post("/ui/call-all", response_class=HTMLResponse)
async def ui_call_all() -> str:
    try:
        results = await manager.call_all()
        return _results_html(results)
    except Exception as exc:
        return f'<span class="error">{html.escape(str(exc))}</span>'


@app.post("/ui/hangup-all", response_class=HTMLResponse)
async def ui_hangup_all() -> str:
    return _results_html(await manager.hangup_all())


@app.post("/ui/conference-all", response_class=HTMLResponse)
async def ui_start_all_conferences() -> str:
    try:
        return _results_html(await manager.start_all_conferences())
    except Exception as exc:
        return f'<span class="error">{html.escape(str(exc))}</span>'


@app.get("/ui/settings", response_class=HTMLResponse)
async def ui_settings() -> str:
    fields: list[str] = []
    for item in runtime_settings.describe():
        disabled = " disabled" if item["locked"] else ""
        locked = " (bloqueado por entorno)" if item["locked"] else ""
        if item["kind"] == "bool":
            checked = " checked" if item["value"] else ""
            control = (
                f'<input type="checkbox" name="{item["key"]}" value="true"'
                f"{checked}{disabled}>"
            )
        else:
            minimum = f' min="{item["minimum"]}"' if item["minimum"] is not None else ""
            maximum = f' max="{item["maximum"]}"' if item["maximum"] is not None else ""
            step = ' step="0.5"' if item["kind"] == "float" else ""
            control = (
                f'<input type="number" name="{item["key"]}" value="{item["value"]}"'
                f"{minimum}{maximum}{step}{disabled}>"
            )
        fields.append(f'<label>{html.escape(item["label"] + locked)}{control}</label>')
    return (
        '<p>Recuperación tras reinicio: pausa segura con confirmación manual.</p>'
        '<form hx-post="/ui/settings" hx-target="#settings-result">'
        + "".join(fields)
        + '<button type="submit">Guardar ajustes</button></form>'
        + '<div id="settings-result" class="result" aria-live="polite"></div>'
    )


@app.post("/ui/settings", response_class=HTMLResponse)
async def ui_update_settings(
    poll_interval: float | None = Form(default=None),
    conference_call_timeout: float | None = Form(default=None),
    conference_merge_timeout: float | None = Form(default=None),
    max_conference_calls: int | None = Form(default=None),
    merge_attempts: int | None = Form(default=None),
    auto_speaker: str | None = Form(default=None),
    auto_merge: str | None = Form(default=None),
    skip_failed_numbers: str | None = Form(default=None),
) -> str:
    submitted = {
        "poll_interval": poll_interval,
        "conference_call_timeout": conference_call_timeout,
        "conference_merge_timeout": conference_merge_timeout,
        "max_conference_calls": max_conference_calls,
        "merge_attempts": merge_attempts,
        "auto_speaker": auto_speaker is not None,
        "auto_merge": auto_merge is not None,
        "skip_failed_numbers": skip_failed_numbers is not None,
    }
    updates = {
        key: value
        for key, value in submitted.items()
        if key not in runtime_settings.locked and value is not None
    }
    try:
        await runtime_settings.update(updates)
        manager.apply_settings(runtime_settings.values)
        return '<span class="success">Ajustes guardados y aplicados</span>'
    except Exception as exc:
        return f'<span class="error">{html.escape(str(exc))}</span>'


@app.post("/ui/devices/{serial}/resume", response_class=HTMLResponse)
async def ui_resume_conference(serial: str) -> str:
    try:
        total = await manager.resume_conference(serial)
        return f'<span class="success">Sesión reanudada con {total} pendientes</span>'
    except Exception as exc:
        return f'<span class="error">{html.escape(str(exc))}</span>'


@app.post("/ui/devices/{serial}/cancel-recovered", response_class=HTMLResponse)
async def ui_cancel_recovered_conference(serial: str) -> str:
    try:
        await manager.cancel_recovered_conference(serial)
        return '<span class="success">Sesión recuperada cancelada</span>'
    except Exception as exc:
        return f'<span class="error">{html.escape(str(exc))}</span>'


def _parse_text_numbers(value: str) -> list[str]:
    return [item.strip() for item in re.split(r"[,;\r\n]+", value) if item.strip()]


def _parse_csv_numbers(value: str) -> list[str]:
    rows = csv.reader(io.StringIO(value))
    numbers: list[str] = []
    for index, row in enumerate(rows):
        if not row or not row[0].strip():
            continue
        cell = row[0].strip()
        if index == 0 and cell.casefold() in {"number", "phone", "numero", "número"}:
            continue
        numbers.append(cell)
    return numbers


def _device_card(snapshot) -> str:
    serial = html.escape(snapshot.serial)
    serial_path = quote(snapshot.serial, safe="")
    dom_id = re.sub(r"[^A-Za-z0-9_-]", "_", snapshot.serial)
    model = html.escape(snapshot.model)
    state_class = snapshot.call_state.value.lower()
    availability = snapshot.adb_state.value if snapshot.connected else "disconnected"
    disabled = "" if snapshot.connected and snapshot.adb_state is AdbState.DEVICE else " disabled"
    version = html.escape(snapshot.android_version or "sin leer")
    recovery_controls = ""
    if snapshot.conference_state.value == "RECOVERED_PAUSED":
        recovery_controls = f"""
        <button type="button" hx-post="/ui/devices/{serial_path}/resume" hx-target="#result-{dom_id}"{disabled}>Reanudar conferencia</button>
        <button type="button" class="danger" hx-post="/ui/devices/{serial_path}/cancel-recovered" hx-target="#result-{dom_id}">Cancelar sesión recuperada</button>"""
    return f"""
    <article class="device-card">
      <div class="card-head"><h3>{model}</h3><span class="status {state_class}">{snapshot.call_state.value}</span></div>
      <code>{serial}</code>
      <p>ADB: <strong>{availability}</strong> · Android: {version} · Pendientes: {snapshot.pending_numbers}</p>
      <p>Conferencia: <strong>{snapshot.conference_state.value}</strong> · Progreso: {snapshot.conference_progress}/{snapshot.conference_total}</p>
      {f'<p class="error">{html.escape(snapshot.conference_error)}</p>' if snapshot.conference_error else ''}
      <form hx-post="/ui/devices/{serial_path}/call" hx-target="#result-{dom_id}" hx-swap="innerHTML">
        <label>Número <input id="number-{dom_id}" name="number" type="tel" inputmode="tel" pattern="\\+?[0-9]{{3,20}}" title="Use entre 3 y 20 dígitos, con + opcional al inicio" required placeholder="8090000000" hx-preserve oninput="this.value=this.value.replace(/[^0-9+]/g, '').replace(/(?!^)\\+/g, '')"{disabled}></label>
        <button type="submit"{disabled}>Llamar</button>
        <button type="button" hx-post="/ui/devices/{serial_path}/add-call" hx-include="#number-{dom_id}" hx-target="#result-{dom_id}"{disabled}>Agregar llamada</button>
        <button type="button" hx-get="/ui/devices/{serial_path}/calls" hx-target="#result-{dom_id}"{disabled}>Ver llamadas</button>
        <button type="button" hx-post="/ui/devices/{serial_path}/merge" hx-target="#result-{dom_id}"{disabled}>Combinar</button>
        <button type="button" hx-post="/ui/devices/{serial_path}/speaker" hx-target="#result-{dom_id}"{disabled}>Activar altavoz</button>
        {recovery_controls}
        <button type="button" class="danger" hx-post="/ui/devices/{serial_path}/hangup" hx-target="#result-{dom_id}"{disabled}>Colgar</button>
      </form>
      <div id="result-{dom_id}" class="result" aria-live="polite"></div>
    </article>"""


def _results_html(results: dict) -> str:
    if not results:
        return '<span class="error">No hay dispositivos disponibles</span>'
    summary = ", ".join(
        f"{html.escape(serial)}: {'OK' if result['ok'] else html.escape(result['error'])}"
        for serial, result in results.items()
    )
    return f'<span class="success">{summary}</span>'


def _http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, KeyError):
        return HTTPException(404, str(exc).strip("'"))
    if isinstance(exc, (RuntimeError, DeviceUnavailableError)):
        return HTTPException(409, str(exc))
    if isinstance(exc, ValueError):
        return HTTPException(400, str(exc))
    if isinstance(exc, AdbError):
        return HTTPException(502, str(exc))
    return HTTPException(500, str(exc))


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("api:app", host=settings.host, port=settings.port, reload=False)
