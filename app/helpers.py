from typing import Any, Dict, Optional, List
from fastapi import FastAPI, Query, Body, HTTPException
from datetime import datetime, timezone

API_CIMA_AEMPS_VERSION = "1.23"

def _build_metadata(
    parametros_busqueda: Dict[str, Any],
    version_api: str = API_CIMA_AEMPS_VERSION
) -> Dict[str, Any]:
    """
    Construye la estructura de metadatos común para las respuestas.
    """
    fecha_hoy = datetime.now(timezone.utc).strftime("%d/%m/%Y %H:%M UTC")
    return {
        "metadata": {
            "fuente": "CIMA (AEMPS)",
            "fecha_consulta": fecha_hoy,
            "parametros_busqueda": parametros_busqueda,
            "version_api": version_api,
            "descargo_responsabilidad": {
                "texto": "Esta información no constituye consejo médico; se proporciona solo a efectos informativos.",
                "uso_responsable": "Consulte siempre con un profesional sanitario antes de tomar decisiones médicas."
            }
        }
    }