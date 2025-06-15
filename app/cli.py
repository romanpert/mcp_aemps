# app/cli.py – CLI para el servidor MCP-AEMPS
"""💊 CLI del servidor MCP-AEMPS (Agencia Española de Medicamentos y Productos Sanitarios).

Comandos principales
--------------------
• **up**      → arranca el servidor en *modo producción* (sin autoreload).
• **dev**     → arranca el servidor en *modo desarrollo* (con --reload).
• **down**    → detiene un servidor que esté corriendo en background mediante `up`.
• **status**  → comprueba si el servidor está en marcha.
• **restart** → reinicia el servidor (down && up) con los mismos parámetros.
• **logs**    → monitoriza en tiempo real un archivo de logs.
• **health**  → consulta el endpoint `/health` y muestra el estado.
• **openapi** → descarga la especificación OpenAPI (`/openapi.json`).
• **docs**    → abre la documentación Swagger UI en el navegador.

En `.mcp_aemps.json` se diferencian:
  - `uvicorn_host`: dirección donde bindea Uvicorn (p.ej. "0.0.0.0").
  - `access_host`: host que usan los clientes para acceder (p.ej. "localhost").
  - `port`: puerto TCP.
"""
from __future__ import annotations

import os
import sys
import subprocess
import webbrowser
import json
import socket
from pathlib import Path
from typing import Optional, Tuple

import typer
import uvicorn
import httpx
from rich.console import Console
from rich.panel import Panel
from rich.align import Align

console = Console()
APP_IMPORT = "app.mcp_aemps_server:app"
DEFAULT_UVICORN_HOST = "0.0.0.0"
DEFAULT_ACCESS_HOST = "localhost"
DEFAULT_PORT = 8000
PID_FILE = Path(".mcp_aemps.pid")
CONFIG_FILE = Path(".mcp_aemps.json")

cli = typer.Typer(add_completion=False, help="CLI del servidor MCP-AEMPS (AEMPS/CIMA)")


def _banner() -> None:
    title_art = """[bold red]███╗   ███╗ ██████╗██████╗      █████╗ ███████╗███╗   ███╗██████╗ ███████╗
████╗ ████║██╔════╝██╔══██╗    ██╔══██╗██╔════╝████╗ ████║██╔══██╗██╔════╝
██╔████╔██║██║     ██████╔╝    ███████║█████╗  ██╔████╔██║██████╔╝███████╗
██║╚██╔╝██║██║     ██╔═══╝     ██╔══██║██╔══╝  ██║╚██╔╝██║██╔═══╝ ╚════██║
██║ ╚═╝ ██║╚██████╗██║         ██║  ██║███████╗██║ ╚═╝ ██║██║     ███████║
╚═╝     ╚═╝ ╚═════╝╚═╝         ╚═╝  ╚═╝╚══════╝╚═╝     ╚═╝╚═╝     ╚══════╝[/bold red]"""
    content = f"""{title_art}

              [bold white]🏥  AGENCIA ESPAÑOLA DE MEDICAMENTOS[/bold white]
                    [bold white]Y PRODUCTOS SANITARIOS[/bold white]

                  [bold white]💊  Centro de Información[/bold white]
                   [bold white]de Medicamentos Autorizados - CIMA[/bold white]"""
    panel = Panel(
        Align.center(content),
        border_style="bright_black",
        padding=(1, 2),
        title="[bold bright_white]Servidor MCP NO OFICIAL de la AEMPS[/bold bright_white]",
        title_align="center",
    )
    console.print("")
    console.print(panel)
    console.print("")

    # Información adicional con enlace clickable
    info_text = (
        "[bold white]La información que devuelve este servidor puedes encontrarla también en:[/bold white]\n"
        "[link=https://cima.aemps.es/cima/publico/home.html]https://cima.aemps.es/cima/publico/home.html[/link]"
    )
    info_panel = Panel(
        Align.center(info_text),
        border_style="bright_black",
        padding=(1, 2),
    )
    console.print(info_panel)
    console.print("")


def _load_config() -> Tuple[str, str, int]:
    """Carga uvicorn_host, access_host y port del fichero de configuración si existe."""
    if CONFIG_FILE.exists():
        try:
            cfg = json.loads(CONFIG_FILE.read_text())
            uvh = cfg.get("uvicorn_host", DEFAULT_UVICORN_HOST)
            acc = cfg.get("access_host", DEFAULT_ACCESS_HOST)
            port = cfg.get("port", DEFAULT_PORT)
            return uvh, acc, port
        except json.JSONDecodeError:
            pass
    return DEFAULT_UVICORN_HOST, DEFAULT_ACCESS_HOST, DEFAULT_PORT


def _save_config(uvicorn_host: str, access_host: str, port: int) -> None:
    """Guarda configuración en disco."""
    try:
        CONFIG_FILE.write_text(
            json.dumps({
                "uvicorn_host": uvicorn_host,
                "access_host": access_host,
                "port": port,
            })
        )
    except Exception:
        console.print(
            "⚠️  No se pudo guardar la configuración en disco.",
            style="yellow",
        )


def _find_free_port(start_port: int, host: str = DEFAULT_UVICORN_HOST) -> int:
    """
    Intenta bindear al puerto `start_port` en `host`; si está ocupado,
    incrementa hasta encontrar uno libre. Devuelve el puerto libre.
    """
    port = start_port
    while True:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            # Evitar errores de TIME_WAIT
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind((host, port))
                return port
            except OSError:
                port += 1


@cli.callback(invoke_without_command=True)
def main(ctx: typer.Context):
    if ctx.invoked_subcommand is None:
        console.print(ctx.get_help())
        raise typer.Exit()


@cli.command()
def up(
    uvicorn_host: str = typer.Option(
        DEFAULT_UVICORN_HOST, help="Dirección donde bindeará Uvicorn"
    ),
    access_host: str = typer.Option(
        DEFAULT_ACCESS_HOST, help="Host público para acceder a la API"
    ),
    port: int = typer.Option(
        DEFAULT_PORT, help="Puerto TCP"
    ),
    workers: int = typer.Option(
        1, help="Número de workers Uvicorn"
    ),
    log_level: str = typer.Option(
        "info", help="Nivel de log Uvicorn"
    ),
    daemon: bool = typer.Option(
        False, "--daemon/--no-daemon", help="Ejecutar en background"
    ),
):
    """Arranca el servidor *sin* autorecarga, orientado a producción."""
    _banner()

    # Comprobar si el puerto está libre; si no, buscar siguiente libre
    puerto_libre = _find_free_port(start_port=port, host=uvicorn_host)
    if puerto_libre != port:
        console.print(
            f"⚠️  El puerto {port} está ocupado; usando puerto libre {puerto_libre}.",
            style="yellow"
        )
        port = puerto_libre

    cmd = [
        sys.executable,
        "-m",
        "uvicorn",
        APP_IMPORT,
        "--host", uvicorn_host,
        "--port", str(port),
        "--workers", str(workers),
        "--log-level", log_level,
    ]
    if daemon:
        proc = subprocess.Popen(cmd)
        PID_FILE.write_text(str(proc.pid))
        console.print(
            f"🚀  Servidor en marcha (PID [bold]{proc.pid}[/]) → http://{access_host}:{port}",
            style="green",
        )
    else:
        console.print("🏁  Ejecutando servidor en foreground… (Ctrl-C para salir)")
        subprocess.run(cmd, check=False)

    # Guardar configuración final (con el puerto potencialmente ajustado)
    _save_config(uvicorn_host, access_host, port)


@cli.command()
def dev(
    uvicorn_host: str = typer.Option(
        DEFAULT_UVICORN_HOST, help="Host (desarrollo)"
    ),
    access_host: str = typer.Option(
        "localhost", help="Host público para acceder a la API"
    ),
    port: int = typer.Option(DEFAULT_PORT, help="Puerto TCP"),
):
    """Arranca el servidor con `--reload` para desarrollo rápido."""
    _banner()

    # Comprobar si el puerto está libre; si no, buscar siguiente libre
    puerto_libre = _find_free_port(start_port=port, host=uvicorn_host)
    if puerto_libre != port:
        console.print(
            f"⚠️  El puerto {port} está ocupado; usando puerto libre {puerto_libre}.",
            style="yellow"
        )
        port = puerto_libre

    console.print("🔄  Modo desarrollo con recarga automática…", style="yellow")
    # Guardar configuración con el puerto utilizado
    _save_config(uvicorn_host, access_host, port)
    uvicorn.run(
        APP_IMPORT,
        host=uvicorn_host,
        port=port,
        reload=True,
        log_level="debug",
    )


@cli.command()
def down():
    """Detiene el servidor iniciado con `up --daemon` (lee PID y config)."""
    if not PID_FILE.exists():
        console.print("⚠️  No hay PID registrado; ¿arrancaste con --daemon?", style="yellow")
        raise typer.Exit(code=1)
    pid = int(PID_FILE.read_text())
    console.print(f"🔻  Enviando SIGTERM al proceso {pid}…")
    try:
        os.kill(pid, 15)
        console.print("🛑  Servidor detenido correctamente.", style="bold red")
    except ProcessLookupError:
        console.print("⚠️  Proceso no encontrado; ya estaba parado.", style="yellow")
    finally:
        PID_FILE.unlink(missing_ok=True)
        CONFIG_FILE.unlink(missing_ok=True)


@cli.command()
def status():
    """Comprueba si el servidor está en marcha."""
    if not PID_FILE.exists():
        console.print(f"❌  No hay servidor en ejecución.", style="red")
        raise typer.Exit(code=1)
    pid = int(PID_FILE.read_text())
    try:
        os.kill(pid, 0)
        uvh, acc, port = _load_config()
        console.print(
            f"✅  Servidor activo (PID {pid}) en http://{acc}:{port}",
            style="green",
        )
    except OSError:
        console.print(f"❌  No se encontró proceso con PID {pid}.", style="red")
        PID_FILE.unlink(missing_ok=True)
        CONFIG_FILE.unlink(missing_ok=True)
        raise typer.Exit(code=1)


@cli.command()
def restart(
    workers: int = typer.Option(2, help="Número de workers Uvicorn"),
    log_level: str = typer.Option("info", help="Nivel de log Uvicorn"),
    daemon: bool = typer.Option(False, "--daemon/--no-daemon", help="Ejecutar en background"),
    uvicorn_host: Optional[str] = None,
    access_host: Optional[str] = None,
    port: Optional[int] = None,
):
    """Reinicia el servidor (down && up) con los mismos parámetros."""
    console.print("🔄  Reiniciando servidor…", style="yellow")
    u, a, p = _load_config()
    uvicorn_host = uvicorn_host or u
    access_host = access_host or a
    port = port or p
    try:
        down()
    except typer.Exit:
        pass
    up(
        uvicorn_host=uvicorn_host,
        access_host=access_host,
        port=port,
        workers=workers,
        log_level=log_level,
        daemon=daemon,
    )


@cli.command()
def logs(
    file: Path = typer.Option(..., exists=True, readable=True, help="Ruta al archivo de log"),
):
    """Muestra en tiempo real el contenido de un archivo de log."""
    console.print(f"📜  Mostrando logs desde [bold]{file}[/], presiona Ctrl-C... ")
    subprocess.run(["tail", "-f", str(file)])


@cli.command()
def health(
    access_host: Optional[str] = typer.Option(None, help="Host público para acceder a la API"),
    port: Optional[int] = typer.Option(None, help="Puerto donde se ejecuta la API"),
):
    """Consulta el endpoint /health y muestra el JSON de respuesta."""
    _, acc, p = _load_config()
    host = access_host or acc
    port = port or p
    url = f"http://{host}:{port}/health"
    console.print(f"🔍  Consultando {url}…")
    try:
        resp = httpx.get(url, timeout=5.0)
        resp.raise_for_status()
        console.print(resp.json())
    except Exception as e:
        console.print(f"❌  Error consultando /health: {e}", style="red")
        raise typer.Exit(code=1)


@cli.command()
def openapi(
    output: Path = typer.Option("openapi.json", help="Fichero de salida"),
    access_host: Optional[str] = typer.Option(None, help="Host público"),
    port: Optional[int] = typer.Option(None, help="Puerto API"),
):
    """Descarga la especificación OpenAPI, la guarda y abre en navegador."""
    _, acc, p = _load_config()
    host = access_host or acc
    port = port or p
    url = f"http://{host}:{port}/openapi.json"
    console.print(f"📥  Descargando spec desde {url}…")
    try:
        resp = httpx.get(url, timeout=10.0)
        resp.raise_for_status()
        output.write_text(resp.text)
        console.print(f"✅  Spec guardada en [bold]{output}[/].")
        # Abrir el JSON en el navegador por defecto
        file_url = output.resolve().as_uri()
        console.print(f"🌐  Abriendo spec en {file_url}…")
        webbrowser.open(file_url)
    except Exception as e:
        console.print(f"❌  Error descargando o abriendo OpenAPI: {e}", style="red")
        raise typer.Exit(code=1)


@cli.command()
def docs(
    access_host: Optional[str] = typer.Option(None, help="Host público"),
    port: Optional[int] = typer.Option(None, help="Puerto API"),
):
    """Abre la Swagger UI en el navegador."""
    _, acc, p = _load_config()
    host = access_host or acc
    port = port or p
    url = f"http://{host}:{port}/docs"
    console.print(f"🌐  Abriendo docs en {url}…")
    try:
        webbrowser.open(url)
    except Exception as e:
        console.print(f"❌  No se pudo abrir el navegador: {e}", style="red")
        raise typer.Exit(code=1)


if __name__ == "__main__":
    cli()
