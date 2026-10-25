# Agente ADB Caller SaaS

Esta carpeta es la unidad compilable del agente. No depende del código legacy de
la raíz; utiliza exclusivamente `saas/core` y `saas/protocol`.

El agente inicia la conexión WSS, observa ADB y ejecuta únicamente las acciones
cerradas del protocolo. Guarda el token en un archivo `0600` y los resultados de
comandos en un journal SQLite para no repetir llamadas tras un reinicio.

## Desarrollo

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e ../core -e ../protocol -e '.[build]'
adbcaller-agent --version
```

## Emparejar y ejecutar

```bash
adbcaller-agent pair --server https://saas.example.com --name puesto-linux-1
adbcaller-agent run
```

El código de pairing se solicita sin mostrarlo en la terminal. Para desarrollo
en una LAN se admite `http://IP_DEL_SERVIDOR:8000`; no debe exponerse así a
Internet. La configuración predeterminada queda en
`~/.config/adbcaller-agent/config.json`.

## Compilar

Desde `saas/agent`:

```bash
pyinstaller --clean packaging/agent.spec
```

Para incluir platform-tools sin versionarlos:

```bash
ADB_PLATFORM_TOOLS=/ruta/a/platform-tools \
  pyinstaller --clean packaging/agent.spec
```

El build debe ejecutarse por separado en Windows y Linux. Antes de distribuir
ADB se debe fijar su versión y verificar la licencia y el SHA-256 del paquete
oficial.

## Servicio Linux

Después de emparejar y copiar el ejecutable/configuración:

```bash
sudo useradd --system --create-home --groups plugdev adbcaller
sudo install -d -o adbcaller -g adbcaller -m 700 /var/lib/adbcaller-agent
sudo install -d -m 755 /opt/adbcaller-agent
sudo install -m 755 dist/adbcaller-agent /opt/adbcaller-agent/adbcaller-agent
sudo install -m 644 packaging/adbcaller-agent.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now adbcaller-agent
```

Copie `config.json` a `/var/lib/adbcaller-agent/config.json` con propietario
`adbcaller:adbcaller` y modo `0600`. Las reglas `udev` deben permitir al grupo
`plugdev` acceder al A56 sin ejecutar el agente como root.
