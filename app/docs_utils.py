"""
Script para descargar el listado de Presentaciones de la AEMPS.
"""

import os
import requests

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

