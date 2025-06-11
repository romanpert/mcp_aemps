"""
Script para descargar el listado de Presentaciones de la AEMPS.
"""

import os
import re
import requests
import logging
from datetime import datetime
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

def download_presentaciones(dest_path="data/documentacion/Presentaciones.xls"):
    """
    Descarga el fichero Excel de presentaciones desde la AEMPS
    y lo guarda en la ruta local especificada.
    """
    url = "https://listadomedicamentos.aemps.gob.es/Presentaciones.xls"
    # Crear directorio si no existe
    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    
    # Petición HTTP para descargar el fichero
    resp = requests.get(url, stream=True)
    resp.raise_for_status()
    
    # Guardar el contenido en modo binario
    with open(dest_path, "wb") as f:
        for chunk in resp.iter_content(chunk_size=8192):
            if chunk:
                f.write(chunk)
    
    # print(f"Fichero descargado y guardado en: {dest_path}")

def download_nomenclator_csv(
    dest_dir: str = "data/documentacion",
    url: str = (
        "https://listadomedicamentos.aemps.gob.es/"
        "nomenclator.do?metodo=buscarProductos&especialidad=%%%&d-4015021-e=1&6578706f7274=1"
    ),
    timeout: int = 30
) -> str:
    """
    Descarga el CSV de Nomenclátor de la AEMPS a dest_dir.  
    - Extrae el filename de Content-Disposition o de Last-Modified/url.  
    - Compara prefijo YYYYMMDD con los existentes.  
    - Si existe uno igual o más reciente, devuelve esa ruta.  
    - Si no, borra solo los CSVs antiguos de nomenclátor y guarda el nuevo.  
    Devuelve la ruta al CSV final.
    """
    os.makedirs(dest_dir, exist_ok=True)

    # 1) Obtengo cabeceras con una petición ligera (HEAD) si el servidor lo soporta
    try:
        head = requests.head(url, timeout=timeout)
        head.raise_for_status()
    except Exception:
        head = None  # caigo luego a GET completo

    # 2) Descargo con GET (streaming)
    resp = requests.get(url, stream=True, timeout=timeout)
    resp.raise_for_status()

    # 3) Determino filename
    filename = None
    if head is not None:
        cd = head.headers.get("content-disposition", "")
    else:
        cd = resp.headers.get("content-disposition", "")
    m = re.search(r'filename="?([^"]+)"?', cd)
    if m:
        filename = m.group(1)
        logger.debug("Filename from Content-Disposition: %s", filename)

    if not filename:
        # Fallback: uso Last-Modified o UTC ahora
        last_mod = (head or resp).headers.get("last-modified")
        if last_mod:
            try:
                dt = parsedate_to_datetime(last_mod)
            except Exception:
                dt = datetime.utcnow()
        else:
            dt = datetime.utcnow()
        date_str = dt.strftime("%Y%m%d")

        # extraigo un basename razonable de la URL
        path = urlparse(url).path
        base = os.path.basename(path) or "nomenclator.csv"
        filename = f"{date_str}_{base}"
        logger.debug("Fallback filename: %s", filename)

    # 4) Extraigo prefijo fecha YYYYMMDD
    mdate = re.match(r"(\d{8})", filename)
    new_date = mdate.group(1) if mdate else None

    # 5) Reviso CSVs existentes
    existing = [f for f in os.listdir(dest_dir) if f.lower().endswith(".csv")]
    for f in existing:
        # extraigo su fecha
        mf = re.match(r"(\d{8})", f)
        if mf and new_date and mf.group(1) >= new_date:
            logger.info("Ya existe CSV con fecha %s: %s, no descargo.", mf.group(1), f)
            return os.path.join(dest_dir, f)

    # 6) Borro solo los CSVs antiguos de nomenclátor
    for f in existing:
        mf = re.match(r"(\d{8})", f)
        if not mf or (new_date and mf.group(1) < new_date):
            try:
                os.remove(os.path.join(dest_dir, f))
                logger.debug("CSV antiguo borrado: %s", f)
            except Exception:
                logger.warning("No se pudo borrar viejo CSV: %s", f)

    # 7) Grabo el nuevo fichero
    dest_path = os.path.join(dest_dir, filename)
    with open(dest_path, "wb") as fd:
        for chunk in resp.iter_content(chunk_size=8192):
            if chunk:
                fd.write(chunk)

    logger.info("Descargado nuevo CSV a: %s", dest_path)
    return dest_path