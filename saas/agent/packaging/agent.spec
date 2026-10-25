from pathlib import Path
import os


agent_dir = Path(SPECPATH).parent
entrypoint = agent_dir / "adbcaller_agent" / "main.py"
binaries = []
platform_tools = os.getenv("ADB_PLATFORM_TOOLS")
if platform_tools:
    tools_dir = Path(platform_tools)
    if not tools_dir.is_dir():
        raise ValueError("ADB_PLATFORM_TOOLS no apunta a un directorio")
    binaries = [(str(path), "platform-tools") for path in tools_dir.iterdir() if path.is_file()]

analysis = Analysis(
    [str(entrypoint)],
    pathex=[str(agent_dir)],
    binaries=binaries,
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(analysis.pure)
exe = EXE(
    pyz,
    analysis.scripts,
    analysis.binaries,
    analysis.datas,
    [],
    name="adbcaller-agent",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
)
