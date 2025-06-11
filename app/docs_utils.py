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
    return (
        "https://listadomedicamentos.aemps.gob.es/"
        "nomenclator.do?metodo=buscarProductos&especialidad=%%%&d-4015021-e=1&6578706f7274=1"
    )


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
    timeout: int = 60
) -> Path:
    """
    Descarga asíncrona del CSV de Nomenclátor, gestiona filenames y caché local:
    - Usa HEAD para extraer Content-Disposition.
    - Si existe CSV igual o más reciente, no descarga.
    - Borra CSVs antiguos si procede.
    """
    if url is None:
        url = get_nomenclator_url()
    dest_dir.mkdir(parents=True, exist_ok=True)

    async with httpx.AsyncClient(timeout=timeout) as client:
        # 1) HEAD
        try:
            head = await client.head(url, follow_redirects=True)
            head.raise_for_status()
        except Exception:
            head = None

        # 2) GET
        resp = await client.get(url, follow_redirects=True)
        resp.raise_for_status()

    # 3) Determinar filename
    filename = None
    cd_header = head.headers.get("content-disposition", "") if head else resp.headers.get("content-disposition", "")
    m = re.search(r'filename="?([^\";]+)"?', cd_header)
    if m:
        filename = m.group(1)
        logger.debug(f"Filename from Content-Disposition: {filename}")

    if not filename:
        last_mod = (head or resp).headers.get("last-modified")
        if last_mod:
            try:
                dt = parsedate_to_datetime(last_mod)
            except Exception:
                dt = datetime.utcnow()
        else:
            dt = datetime.utcnow()
        date_str = dt.strftime("%Y%m%d")
        path = urlparse(url).path
        base = os.path.basename(path) or "nomenclator.csv"
        filename = f"{date_str}_{base}"
        logger.debug(f"Fallback filename: {filename}")

    # 4) Comparar con existentes
    prefix = re.match(r"(\d{8})", filename)
    new_date = prefix.group(1) if prefix else None
    existing = [f for f in os.listdir(dest_dir) if f.lower().endswith(".csv")]
    for f in existing:
        mf = re.match(r"(\d{8})", f)
        if mf and new_date and mf.group(1) >= new_date:
            logger.info(f"CSV existente más reciente o igual: {f}, omitiendo descarga.")
            return dest_dir / f

    # 5) Borrar CSVs antiguos
    for f in existing:
        mf = re.match(r"(\d{8})", f)
        if not mf or (new_date and mf.group(1) < new_date):
            try:
                os.remove(dest_dir / f)
                logger.debug(f"CSV antiguo borrado: {f}")
            except Exception:
                logger.warning(f"No se pudo borrar viejo CSV: {f}")

    # 6) Guardar nuevo
    dest_path = dest_dir / filename
    with open(dest_path, "wb") as fd:
        for chunk in resp.iter_bytes(chunk_size=8192):
            fd.write(chunk)
    logger.info(f"Descargado nuevo CSV a: {dest_path}")

    return dest_path
