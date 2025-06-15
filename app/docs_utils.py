# app/docs_utils.py
"""
Utilidades asíncronas para obtener y gestionar descargas de la AEMPS.
"""
import os
import re
import logging
from datetime import datetime
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse
from pathlib import Path
import httpx
import asyncio

logger = logging.getLogger(__name__)


def get_presentaciones_url() -> str:
    """
    URL para descargar Presentaciones de la AEMPS.
    """
    return "https://listadomedicamentos.aemps.gob.es/Presentaciones.xls"


def get_nomenclator_url() -> str:
    """
    URL para descargar el CSV de Nomenclátor de la AEMPS.
    """
    # Construimos la URL con parametros, para que httpx haga el escape correcto
    base = "https://www.sanidad.gob.es/profesionales/nomenclator.do"
    params = {
        "metodo": "buscarProductos",
        "especialidad": "%%%",      # httpx lo convertirá en %25%25%25
        "d-4015021-e": "1",
        "6578706f7274": "1",
    }
    # Devolvemos un string; httpx.URL(params=...) formatea bien la query
    return str(httpx.URL(base, params=params))


async def download_presentaciones(dest_path: Path, timeout: int = 60) -> Path:
    """
    Descarga asíncrona de Presentaciones.xls y guarda en dest_path.
    """
    url = get_presentaciones_url()
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.get(url, follow_redirects=True)
        resp.raise_for_status()
        dest_path.write_bytes(resp.content)
    logger.info(f"Descargado Presentaciones.xls a: {dest_path}")
    return dest_path


async def download_nomenclator_csv(
    dest_dir: Path,
    url: str = None,
    timeout: int = 60,
    max_retries: int = 3
) -> Path:
    """
    Descarga asíncrona del CSV de Nomenclátor, gestiona filenames y caché local:
    - Usa HEAD para extraer Content-Disposition.
    - Si existe CSV igual o más reciente, no descarga.
    - Borra CSVs antiguos si procede.
    - Timeout diferenciado, streaming y retries.
    """
    if url is None:
        url = get_nomenclator_url()
    url = url.strip()
    dest_dir.mkdir(parents=True, exist_ok=True)

    # Configuramos timeout: 10s de conexión, `timeout` s de lectura
    timeout_cfg = httpx.Timeout(connect=10.0, read=float(timeout), write=60.0, pool=None)

    for attempt in range(1, max_retries + 1):
        try:
            async with httpx.AsyncClient(timeout=timeout_cfg) as client:
                # 1) HEAD para Content-Disposition
                head = None
                try:
                    head = await client.head(url, follow_redirects=True)
                    head.raise_for_status()
                except httpx.HTTPError:
                    logger.debug("HEAD falló; seguiremos con GET")

                # 2) Streaming GET
                async with client.stream("GET", url, follow_redirects=True) as resp:
                    resp.raise_for_status()

                    # 3) Determinar filename
                    cd = (
                        (head.headers.get("content-disposition", ""))
                        if head
                        else resp.headers.get("content-disposition", "")
                    )
                    m = re.search(r'filename="?([^\";]+)"?', cd)
                    if m:
                        filename = m.group(1)
                        logger.debug(f"Filename from Content-Disposition: {filename}")
                    else:
                        last_mod = (head or resp).headers.get("last-modified", "")
                        try:
                            dt = parsedate_to_datetime(last_mod) if last_mod else datetime.utcnow()
                        except Exception:
                            dt = datetime.utcnow()
                        date_str = dt.strftime("%Y%m%d")
                        base = os.path.basename(urlparse(url).path) or "nomenclator.csv"
                        filename = f"{date_str}_{base}"
                        logger.debug(f"Fallback filename: {filename}")

                    # 4) Comprobar caché local
                    prefix = re.match(r"(\d{8})", filename)
                    new_date = prefix.group(1) if prefix else None
                    existing = [f for f in os.listdir(dest_dir) if f.lower().endswith(".csv")]
                    for f in existing:
                        mf = re.match(r"(\d{8})", f)
                        if mf and new_date and mf.group(1) >= new_date:
                            logger.info(f"CSV existente más reciente o igual: {f}, omitiendo descarga.")
                            return dest_dir / f

                    # 5) Borrar antiguos
                    for f in existing:
                        mf = re.match(r"(\d{8})", f)
                        if not mf or (new_date and mf.group(1) < new_date):
                            try:
                                os.remove(dest_dir / f)
                                logger.debug(f"CSV antiguo borrado: {f}")
                            except Exception:
                                logger.warning(f"No se pudo borrar viejo CSV: {f}")

                    # 6) Escribir nuevo archivo por chunks
                    dest_path = dest_dir / filename
                    with open(dest_path, "wb") as fd:
                        async for chunk in resp.aiter_bytes(chunk_size=32_768):
                            fd.write(chunk)

                    logger.info(f"Descargado nuevo CSV a: {dest_path} ({dest_path.stat().st_size} bytes)")
                    return dest_path

        except (httpx.ReadTimeout, httpx.ConnectTimeout) as e:
            logger.warning(f"Timeout en intento {attempt}/{max_retries}: {e}")
            if attempt < max_retries:
                backoff = 2 ** (attempt - 1)
                logger.info(f"Esperando {backoff}s antes de reintentar…")
                await asyncio.sleep(backoff)
            else:
                logger.error("Agotados reintentos por timeout, aborto descarga.")
                raise

        except httpx.HTTPError as e:
            logger.error(f"Error HTTP en descarga: {e}")
            raise

    # Nunca debería llegar aquí
    raise RuntimeError("No fue posible descargar el CSV de nomenclátor.")