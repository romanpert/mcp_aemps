# app/helpers

from typing import Any, Dict, Optional, List, Literal
from fastapi import FastAPI, Query, Body, HTTPException
from datetime import datetime, timezone
import httpx
import pandas as pd
import unicodedata
from io import BytesIO
import json
import zipfile
from fastapi.responses import StreamingResponse
import app.cima_client as cima

API_CIMA_AEMPS_VERSION = "1.23"

# VERSION API CIMA
API_PSUM_VERSION = "2.0"

def format_response(resultado: Any, metadatos: Dict[str, Any]) -> Any:
    """
    Formatea la respuesta combinando los datos de resultado con los metadatos:
    - Si resultado es dict, fusiona directamente.
    - Si resultado es lista, aplica fusion para cada elemento.
    - En otros casos, empaqueta el valor en clave "data" junto a los metadatos.
    """
    # Caso: dict
    if isinstance(resultado, dict):
        return {**resultado, **metadatos}

    # Caso: lista
    if isinstance(resultado, list):
        lista_formateada: list[Any] = []
        for item in resultado:
            if isinstance(item, dict):
                lista_formateada.append({**item, **metadatos})
            else:
                lista_formateada.append({"data": item, **metadatos})
        return lista_formateada

    # Caso genérico
    return {"data": resultado, **metadatos}

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

def _filter_exact(df: pd.DataFrame, column: str, value: str) -> pd.DataFrame:
    return df[df[column].astype(str) == value]


def _filter_contains(df: pd.DataFrame, column: str, value: str) -> pd.DataFrame:
    return df[df[column].str.contains(value, case=False, na=False)]


def _filter_bool(df: pd.DataFrame, column: str, flag: bool) -> pd.DataFrame:
    val = "SI" if flag else "NO"
    return df[df[column] == val]


def _filter_numeric(df: pd.DataFrame, column: str, min_val: Optional[float], max_val: Optional[float]) -> pd.DataFrame:
    if min_val is not None:
        df = df[df[column].astype(float) >= min_val]
    if max_val is not None:
        df = df[df[column].astype(float) <= max_val]
    return df

def _paginate(df: pd.DataFrame, page: int, page_size: int) -> pd.DataFrame:
    start = (page - 1) * page_size
    return df.iloc[start:start + page_size]

def _filter_date(df: pd.DataFrame, column: str, date_str: str, op: str) -> pd.DataFrame:
    d = datetime.strptime(date_str, "%d/%m/%Y")
    series = pd.to_datetime(df[column], dayfirst=True)
    if op == 'ge':
        return df[series >= d]
    else:
        return df[series <= d]

# AUX FUNCTION

def _normalize(s: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", s.lower())
        if unicodedata.category(c) != "Mn"
    )
# Helper para llamadas seguras a cima.*
async def safe_cima_call(func, *args, **kwargs) -> Any:
    try:
        return await func(*args, **kwargs)
    except httpx.HTTPStatusError as exc:
        # Respuesta con status code de la API externa como 502
        status = exc.response.status_code
        text = exc.response.text
        raise HTTPException(
            status_code=502,
            detail=f"Error en API externa ({status}): {text}"
        )
    except httpx.RequestError as exc:
        # Errores de conexión (timeout, DNS, etc.)
        raise HTTPException(
            status_code=502,
            detail=f"No se pudo conectar con la API externa: {exc}"
        )
    except Exception as exc:
        # Cualquier otro fallo inesperado
        raise HTTPException(
            status_code=500,
            detail="Error interno inesperado"
        )