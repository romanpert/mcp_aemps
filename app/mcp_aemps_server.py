# mcp_aemps_server.py – CIMA MCP (Full)
# ================================================================
# Servidor FastAPI-MCP que expone todas las capacidades de la API CIMA
# (AEMPS) como herramientas MCP. Reutiliza el cliente asíncrono `cima_client.py`.
#
# Arranque local:
#     pip install fastapi fastapi-mcp httpx uvicorn fastapi-limiter fastapi-cache redis prometheus-fastapi-instrumentator opentelemetry-instrumentation-fastapi
#     uvicorn mcp_aemps_server:app --reload  # http://localhost:<port>/mcp
#
# Ahora, toda la configuración sensible (hosts, puertos, Redis, CORS, rutas
# de datos, etc.) se lee desde `mcp_aemps.json` y/o variables de entorno.
# ================================================================

from __future__ import annotations
import os
import json
import httpx
import pandas as pd
import asyncio
from datetime import datetime, timezone
from dateutil import parser as date_parser
from pathlib import Path
import tempfile
import shutil
import logging
from typing import Any, List, Optional, AsyncIterator, Tuple, Dict
from httpx import HTTPStatusError
from enum import Enum
from fastapi.responses import HTMLResponse, FileResponse
from fastapi import BackgroundTasks
from fastapi import Body, FastAPI, HTTPException, Query, Depends, Request, Response, WebSocket, Path as FPath
from fastapi_mcp import FastApiMCP
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials, OAuth2PasswordBearer
from fastapi.responses import JSONResponse, StreamingResponse
import zipfile
import io
from io import BytesIO
from pydantic import BaseModel
# Cache
from fastapi_cache import FastAPICache
from fastapi_cache.backends.redis import RedisBackend
from fastapi_cache.decorator import cache
# Rate limiting
from fastapi_limiter import FastAPILimiter
from fastapi_limiter.depends import RateLimiter
# Observability
from prometheus_fastapi_instrumentator import Instrumentator
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

import app.cima_client as cima
import app.mcp_constants as constant
from app.config import settings
from app.startup import lifespan
from app.helpers import (_build_metadata, safe_cima_call, _filter_exact,
                         _paginate, _filter_bool, _filter_contains, _filter_date,
                         _filter_numeric, format_response, _normalize, _html_multiple_zip,
                         API_CIMA_AEMPS_VERSION, API_PSUM_VERSION)

# ------------------------------------------------------------
# 1) Configuración global de logging
# ------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s"
)
logger = logging.getLogger("mcp_aemps_server")

# ------------------------------------------------------------
# Parámetros de Rate Limiting
# ------------------------------------------------------------
RATE_LIMIT = 100        # peticiones permitidas
RATE_PERIOD = 60       # en segundos

# ---------------------------------------------------------------------------
#   Crear la aplicación FastAPI + MCP
# ---------------------------------------------------------------------------
app = FastAPI(
    title="AEMPS CIMA MCP",
    version=settings.mcp_version,
    description="Herramientas MCP sobre la API CIMA de la AEMPS",
    openapi_url="/openapi.json",
    docs_url="/docs",
    redoc_url=None,
    lifespan=lifespan,
    router_dependencies=[Depends(RateLimiter(times=RATE_LIMIT, seconds=RATE_PERIOD))],
    swagger_ui_parameters={"defaultModelsExpandDepth": -1},
)

# app.add_middleware(
#     ProxyHeadersMiddleware,
#     trusted_hosts="*"   # o lista concreta de hosts/proxies
# )

# ---------------------------------------------------------------------------
#   CORS
# ---------------------------------------------------------------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type", "Accept"],
)

# ---------------------------------------------------------------------------
# Middleware adicional (cabeceras de seguridad)
# ---------------------------------------------------------------------------
@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.update({
        "X-Frame-Options": "DENY",
        "X-Content-Type-Options": "nosniff",
    })
    return response

# ---------------------------------------------------------------------------
#   Health & Observability
# ---------------------------------------------------------------------------
@app.get('/health', include_in_schema=False)
async def health():
    return JSONResponse({'status': 'ok'})


Instrumentator().instrument(app).expose(app)
FastAPIInstrumentor.instrument_app(app)

# ---------------------------------------------------------------------------
# 1 · Medicamento (ficha única) — Metadata y formato unificado
# ---------------------------------------------------------------------------
@app.get(
    "/medicamento",
    operation_id="obtener_medicamento",
    summary="Obtener ficha completa de un medicamento (por CN o nº de registro)",
    description=constant.medicamento_description,
    response_model=Dict[str, Any],
)
@cache(expire=3600, key_builder=lambda func, *args, **kwargs: f"medicamento:{kwargs.get('cn','')}:{kwargs.get('nregistro','')}")
async def obtener_medicamento(
    cn: Optional[str] = Query(None, regex=r'^\d+$', description="Código Nacional (CN)."),
    nregistro: Optional[str] = Query(None, regex=r'^\d+$', description="Número de registro AEMPS."),
) -> Dict[str, Any]:
    # 1) Validación de entrada
    if not (cn or nregistro):
        raise HTTPException(400, detail={
            "error": "Parámetros insuficientes",
            "message": "Debe indicar al menos 'cn' o 'nregistro'.",
            "required_params": ["cn", "nregistro"]
        })

    cn_clean = cn and cn.strip()
    nr_clean = nregistro and nregistro.strip()

    logger.info(f"Consultando medicamento – CN: {cn_clean}, NRegistro: {nr_clean}")

    # 2) Llamada segura a CIMA
    try:
        resultado = await safe_cima_call(cima.medicamento, cn=cn_clean, nregistro=nr_clean)
    except HTTPException as exc:
        if exc.status_code == 404:
            raise
        raise HTTPException(
            status_code=exc.status_code,
            detail={
                "error": "Error al obtener medicamento",
                "message": str(exc.detail),
                "support": "Contacte con el administrador si el problema persiste"
            }
        )

    # 3) Post-proceso: parseo de timestamps antes de envolver en format_response
    if isinstance(resultado, dict):
        # 3.1) estado: puede contener aut, susp, rev
        if isinstance(resultado.get("estado"), dict):
            for key, val in list(resultado["estado"].items()):
                resultado["estado"][key] = cima._parse_fecha(val)

        # 3.2) docs[*].fecha
        for doc in resultado.get("docs", []):
            if "fecha" in doc:
                doc["fecha"] = cima._parse_fecha(doc["fecha"])

        # 3.3) fotos[*].fecha
        for foto in resultado.get("fotos", []):
            if "fecha" in foto:
                foto["fecha"] = cima._parse_fecha(foto["fecha"])

        # 3.4) presentaciones[*].estado (cada presentación tiene su propio dict estado)
        for pres in resultado.get("presentaciones", []):
            if isinstance(pres.get("estado"), dict):
                for key, val in list(pres["estado"].items()):
                    pres["estado"][key] = cima._parse_fecha(val)

    # 4) Construcción de metadata
    params = {k: v for k, v in {"cn": cn_clean, "nregistro": nr_clean}.items() if v}
    metadatos = _build_metadata(params)

    # 5) Formato de respuesta
    return format_response(resultado, metadatos)

# ---------------------------------------------------------------------------
# 2 · Medicamentos (listado con filtros) — Metadata adaptada con manejo de errores
# ---------------------------------------------------------------------------
@app.get(
    "/medicamentos",
    operation_id="buscar_medicamentos",
    summary="Listado de medicamentos con filtros regulatorios avanzados",
    description=constant.medicamentos_description,
    response_model=Dict[str, Any],
)
async def buscar_medicamentos(
    nombre: Optional[str] = Query(None, description="Nombre del medicamento (coincidencia parcial o exacta)."),
    laboratorio: Optional[str] = Query(None, description="Nombre del laboratorio fabricante."),
    practiv1: Optional[str] = Query(None, description="Nombre del principio activo principal."),
    practiv2: Optional[str] = Query(None, description="Nombre de un segundo principio activo."),
    idpractiv1: Optional[str] = Query(None, description="ID numérico del principio activo principal."),
    idpractiv2: Optional[str] = Query(None, description="ID numérico de un segundo principio activo."),
    cn: Optional[str] = Query(None, description="Código Nacional del medicamento."),
    atc: Optional[str] = Query(None, description="Código ATC o descripción parcial del mismo."),
    nregistro: Optional[str] = Query(None, description="Número de registro AEMPS."),
    npactiv: Optional[int] = Query(None, description="Número de principios activos asociados al medicamento."),
    triangulo: Optional[int] = Query(None, ge=0, le=1, description="1 = Tienen triángulo, 0 = No tienen triángulo."),
    huerfano: Optional[int] = Query(None, ge=0, le=1, description="1 = Huérfano, 0 = No huérfano."),
    biosimilar: Optional[int] = Query(None, ge=0, le=1, description="1 = Biosimilar, 0 = No biosimilar."),
    sust: Optional[int] = Query(None, ge=1, le=5, description="Tipo de medicamento especial (1–5)."),
    vmp: Optional[str] = Query(None, description="ID del código VMP para buscar equivalentes clínicos."),
    comerc: Optional[int] = Query(None, ge=0, le=1, description="1 = Comercializados, 0 = No comercializados."),
    autorizados: Optional[int] = Query(None, ge=0, le=1, description="1 = Solo autorizados, 0 = Solo no autorizados."),
    receta: Optional[int] = Query(None, ge=0, le=1, description="1 = Con receta, 0 = Sin receta."),
    estupefaciente: Optional[int] = Query(None, ge=0, le=1, description="1 = Incluye estupefacientes, 0 = Excluye."),
    psicotropo: Optional[int] = Query(None, ge=0, le=1, description="1 = Incluye psicótropos, 0 = Excluye."),
    estuopsico: Optional[int] = Query(None, ge=0, le=1, description="1 = Incluye estupefacientes o psicótropos, 0 = Excluye."),
    pagina: Optional[int] = Query(1, ge=1, description="Número de página de resultados (mínimo 1)."),
) -> Dict[str, Any]:
    params = locals()
    logger.info(f"Consultando listado de medicamentos con filtros: {params}")

    # 1) Llamada segura a CIMA
    try:
        resultados = await safe_cima_call(cima.medicamentos, **params)
    except HTTPException as exc:
        if exc.status_code == 502:
            raise HTTPException(
                status_code=502,
                detail={
                    "error": "Error de respuesta de la API CIMA",
                    "message": "La API CIMA devolvió un error al buscar medicamentos",
                    "support": "Contacte con el administrador si el problema persiste"
                }
            )
        if exc.status_code == 500:
            raise HTTPException(
                status_code=500,
                detail={
                    "error": "Error interno del servidor",
                    "message": "Error al consultar el servicio CIMA",
                    "support": "Contacte con el administrador si el problema persiste"
                }
            )
        raise

    # 2) Post-proceso: parseo de fechas en cada resultado
    if isinstance(resultados, dict) and "resultados" in resultados:
        for item in resultados["resultados"]:
            # 2.1) estado puede contener aut, susp, rev...
            estado = item.get("estado")
            if isinstance(estado, dict):
                for fecha_key, ts in list(estado.items()):
                    estado[fecha_key] = cima._parse_fecha(ts)

            # 2.2) docs[*].fecha
            for doc in item.get("docs", []):
                if "fecha" in doc:
                    doc["fecha"] = cima._parse_fecha(doc["fecha"])

            # 2.3) fotos[*].fecha
            for foto in item.get("fotos", []):
                if "fecha" in foto:
                    foto["fecha"] = cima._parse_fecha(foto["fecha"])

    # 3) Construir metadata y devolver
    metadatos = _build_metadata(params)
    return format_response(resultados, metadatos)


# ---------------------------------------------------------------------------
# 3. POST · Ficha técnica (búsqueda de texto) — Metadata adaptada
# ---------------------------------------------------------------------------
@app.post(
    "/ficha-tecnica/buscar",
    operation_id="buscar_en_ficha_tecnica",
    summary="Busca texto en secciones específicas de la ficha técnica",
    description=constant.buscar_ficha_tecnica_description,
    response_model=Dict[str, Any],
)
async def buscar_en_ficha_tecnica(
    reglas: List[Dict[str, Any]] = Body(
        ...,
        description=(
            "Lista de reglas con {seccion, texto, contiene}. "
            "Cada regla debe incluir: 'seccion' en formato 'N' o 'N.N', "
            "'texto' (cadena) y 'contiene' (0 o 1)."
        )
    ),
) -> Dict[str, Any]:
    # 1) Validación de input
    if not isinstance(reglas, list) or not reglas:
        raise HTTPException(
            status_code=400,
            detail="El cuerpo debe ser una lista no vacía de reglas con 'seccion', 'texto' y 'contiene'."
        )
    for regla in reglas:
        if (
            not isinstance(regla, dict)
            or "seccion" not in regla
            or "texto" not in regla
            or "contiene" not in regla
        ):
            raise HTTPException(
                status_code=400,
                detail="Cada regla debe ser un objeto con las claves 'seccion', 'texto' y 'contiene'."
            )
        if regla["contiene"] not in (0, 1):
            raise HTTPException(
                status_code=400,
                detail="El campo 'contiene' debe ser 1 (incluir) o 0 (excluir)."
            )

    # 2) Llamada segura a CIMA
    resultados = await safe_cima_call(cima.buscar_en_ficha_tecnica, reglas)

    # 3) Construcción de metadata y formateo de la respuesta
    metadatos = _build_metadata({"reglas": reglas})
    return format_response(resultados, metadatos)

# ---------------------------------------------------------------------------
# 4 · Presentaciones (listado + detalle) — Metadata adaptada
# ---------------------------------------------------------------------------
@app.get(
    "/presentaciones",
    operation_id="listar_presentaciones",
    summary="Listar presentaciones de un medicamento con filtros (cn, nregistro, etc.)",
    description=constant.presentaciones_description,
    response_model=Dict[str, Any],
)
async def listar_presentaciones(
    cn: Optional[str] = Query(None, description="Código Nacional del medicamento."),
    nregistro: Optional[str] = Query(None, description="Número de registro AEMPS."),
    vmp: Optional[str] = Query(None, description="ID del código VMP para equivalentes clínicos."),
    vmpp: Optional[str] = Query(None, description="ID del código VMPP."),
    idpractiv1: Optional[str] = Query(None, description="ID del principio activo."),
    comerc: Optional[int] = Query(None, ge=0, le=1, description="1 = Comercializados, 0 = No."),
    estupefaciente: Optional[int] = Query(None, ge=0, le=1, description="1 = Incluye estupefacientes, 0 = Excluye."),
    psicotropo: Optional[int] = Query(None, ge=0, le=1, description="1 = Incluye psicótropos, 0 = Excluye."),
    estuopsico: Optional[int] = Query(None, ge=0, le=1, description="1 = Incluye estupefacientes o psicótropos, 0 = Excluye."),
    pagina: Optional[int] = Query(1, ge=1, description="Número de página (mínimo 1)."),
) -> Dict[str, Any]:
    resultados = await safe_cima_call(cima.presentaciones, **locals())
    if resultados is None:
        resultados = {"totalFilas": 0, "pagina": pagina, "tamanioPagina": 0, "resultados": []}

    for item in resultados["resultados"]:
        # 1) estado.* (aut, susp, rev…)
        estado = item.get("estado")
        if isinstance(estado, dict):
            for key, ts in list(estado.items()):
                estado[key] = cima._parse_fecha(ts)

        # 2) docs[*].fecha
        for doc in item.get("docs", []):
            if "fecha" in doc:
                doc["fecha"] = cima._parse_fecha(doc["fecha"])

        # 3) fotos[*].fecha
        for foto in item.get("fotos", []):
            if "fecha" in foto:
                foto["fecha"] = cima._parse_fecha(foto["fecha"])

        # 4) detalleProblemaSuministro.ini/fini (opcional)
        dps = item.get("detalleProblemaSuministro")
        if isinstance(dps, dict):
            if "ini" in dps:
                dps["ini"] = cima._parse_fecha(dps["ini"])
            if "fini" in dps:
                dps["fini"] = cima._parse_fecha(dps["fini"])

    params = {k: v for k, v in {
        "cn": cn, "nregistro": nregistro, "vmp": vmp, "vmpp": vmpp,
        "idpractiv1": idpractiv1, "comerc": comerc, "estupefaciente": estupefaciente,
        "psicotropo": psicotropo, "estuopsico": estuopsico, "pagina": pagina,
    }.items() if v is not None}
    metadatos = _build_metadata(params)
    return format_response(resultados, metadatos)


@app.get(
    "/presentacion",
    operation_id="obtener_presentacion",
    summary="Detalle de una o varias presentaciones (por uno o varios CN)",
    description=constant.presentacion_description,
    response_model=Dict[str, Any],
)
async def obtener_presentacion(
    cn: List[str] = Query(
        ...,
        description="Uno o varios Códigos Nacionales. Repetir: ?cn=123&cn=456"
    )
) -> Dict[str, Any]:
    if not cn:
        raise HTTPException(status_code=400, detail="Debe indicar al menos un 'cn'.")

    # --- caso único ---
    if len(cn) == 1:
        detalle = await safe_cima_call(cima.presentacion, cn[0])
        # parsear todos los timestamps
        if isinstance(detalle, dict):
            # estado.* (aut, susp, rev…)
            for key, ts in list(detalle.get("estado", {}).items()):
                detalle["estado"][key] = cima._parse_fecha(ts)
            # documentos
            for doc in detalle.get("docs", []):
                if "fecha" in doc:
                    doc["fecha"] = cima._parse_fecha(doc["fecha"])
            # fotos
            for foto in detalle.get("fotos", []):
                if "fecha" in foto:
                    foto["fecha"] = cima._parse_fecha(foto["fecha"])

        metadatos = _build_metadata({"cn": cn[0]})
        return format_response(detalle, metadatos)

    # --- caso múltiple ---
    tasks = [safe_cima_call(cima.presentacion, code) for code in cn]
    respuestas = await asyncio.gather(*tasks, return_exceptions=True)

    result_dict: Dict[str, Any] = {}
    errors: Dict[str, Any] = {}

    for code, resp in zip(cn, respuestas):
        if isinstance(resp, Exception):
            errors[code] = {"detail": str(resp)}
            continue

        # parsear todos los timestamps en cada respuesta
        if isinstance(resp, dict):
            for key, ts in list(resp.get("estado", {}).items()):
                resp["estado"][key] = cima._parse_fecha(ts)
            for doc in resp.get("docs", []):
                if "fecha" in doc:
                    doc["fecha"] = cima._parse_fecha(doc["fecha"])
            for foto in resp.get("fotos", []):
                if "fecha" in foto:
                    foto["fecha"] = cima._parse_fecha(foto["fecha"])

        metadatos = _build_metadata({"cn": code})
        # guardar toda la respuesta formateada (datos + metadata)
        result_dict[code] = format_response(resp, metadatos)

    if not result_dict:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "Ninguna presentación encontrada",
                "not_found_cn": list(errors.keys()),
                "errors": errors,
            }
        )

    response = {**result_dict}
    if errors:
        response["errors"] = errors
    return response


# ---------------------------------------------------------------------------
# 6 · VMP/VMPP — Metadata adaptada
# ---------------------------------------------------------------------------
@app.get(
    "/vmpp",
    operation_id="buscar_vmpp",
    summary="Equivalentes clínicos VMP/VMPP filtrables por principio activo, dosis, forma, etc.",
    description=constant.vmpp_description,
    response_model=Dict[str, Any],
)
async def buscar_vmpp(
    practiv1: Optional[str]     = Query(None, description="Nombre del principio activo principal."),
    idpractiv1: Optional[str]   = Query(None, description="ID del principio activo principal."),
    dosis: Optional[str]        = Query(None, description="Dosis del medicamento."),
    forma: Optional[str]        = Query(None, description="Nombre de la forma farmacéutica."),
    atc: Optional[str]          = Query(None, description="Código ATC o descripción parcial."),
    nombre: Optional[str]       = Query(None, description="Nombre del medicamento."),
    modoArbol: Optional[int]    = Query(None, ge=0, le=1, description="0=plano, 1=jerárquico"),
    pagina: Optional[int]       = Query(None, ge=1, description="Número de página (si aplica)"),
) -> Dict[str, Any]:
    # 1) Validación: al menos un criterio
    if not any([practiv1, idpractiv1, dosis, forma, atc, nombre, modoArbol]):
        raise HTTPException(
            status_code=400,
            detail="Se requiere al menos uno de los parámetros: practiv1, idpractiv1, dosis, forma, atc, nombre, modoArbol"
        )

    # 2) Llamada a CIMA
    resultados = await safe_cima_call(cima.vmpp, **locals())

    # 3) Construcción de metadata
    parametros = {k: v for k, v in {
        "practiv1":   practiv1,
        "idpractiv1": idpractiv1,
        "dosis":      dosis,
        "forma":      forma,
        "atc":        atc,
        "nombre":     nombre,
        "modoArbol":  modoArbol,
        "pagina":     pagina,
    }.items() if v is not None}
    metadatos = _build_metadata(parametros)

    # 4) Envolvemos en data + metadata para homogeneidad
    return format_response(resultados, metadatos)


# ---------------------------------------------------------------------------
# 6 · Maestras
# ---------------------------------------------------------------------------
@app.get(
    "/maestras",
    operation_id="consultar_maestras",
    summary="Consultar catálogos maestros: ATC, Principios Activos, Formas, Laboratorios...",
    description=constant.maestras_description,
    response_model=Dict[str, Any],
)
async def consultar_maestras(
    maestra: Optional[int] = Query(None, description="ID de la maestra a consultar."),
    nombre: Optional[str] = Query(None, description="Nombre del elemento a recuperar."),
    id: Optional[str] = Query(None, description="ID del elemento a recuperar."),
    codigo: Optional[str] = Query(None, description="Código del elemento a recuperar."),
    estupefaciente: Optional[int] = Query(None, ge=0, le=1, description="1 = Sólo PA estupefacientes."),
    psicotropo: Optional[int] = Query(None, ge=0, le=1, description="1 = Sólo PA psicótropos."),
    estuopsico: Optional[int] = Query(None, ge=0, le=1, description="PA estupefacientes o psicótropos."),
    enuso: Optional[int] = Query(None, ge=0, le=1, description="0 = PA asociados o no a medicamentos."),
    pagina: Optional[int] = Query(1, ge=1, description="Número de página (si la API lo soporta)."),
) -> Dict[str, Any]:
    resultados = await safe_cima_call(cima.maestras, **locals())

    parametros = {k: v for k, v in {
        "maestra": maestra,
        "nombre": nombre,
        "id": id,
        "codigo": codigo,
        "estupefaciente": estupefaciente,
        "psicotropo": psicotropo,
        "estuopsico": estuopsico,
        "enuso": enuso,
        "pagina": pagina,
    }.items() if v is not None}
    metadatos = _build_metadata(parametros)

    respuesta = format_response(resultados, metadatos)

    return respuesta


# ---------------------------------------------------------------------------
# 7 · Registro de cambios
# ---------------------------------------------------------------------------
TIPO_CAMBIO_MAP = {
    1: "Nuevo",
    2: "Baja",
    3: "Modificado",
}

CAMBIOS_MAP = {
    "estado": "Estado de autorización",
    "comerc":  "Estado de comercialización",
    "prosp":   "Prospecto",
    "ft":      "Ficha técnica",
    "psum":    "Problemas de suministro",
    "notasSeguridad": "Notas de seguridad",
    "matinf":  "Materiales informativos",
    "otros":   "Otros",
}

@app.get(
    "/registro-cambios",
    operation_id="registro_cambios",
    summary="Historial de altas, bajas y modificaciones de medicamentos",
    description=constant.registro_cambios_description,
    response_model=Dict[str, Any],
)
async def registro_cambios(
    fecha: Optional[str] = Query(None, description="Fecha (dd/mm/yyyy)."),
    nregistro: Optional[List[str]] = Query(
        None,
        description="Número de registro AEMPS. Repetir parámetro: ?nregistro=123&nregistro=456"
    ),
    metodo: str = Query("GET", regex="^(GET|POST)$", description="Método HTTP interno."),
) -> Dict[str, Any]:
    resultados = await safe_cima_call(
        cima.registro_cambios,
        fecha=fecha,
        nregistro=nregistro,
        metodo=metodo
    )

    # Si la API devolvió 204 No Content, safe_cima_call devuelve None
    if resultados is None:
        resultados = {"totalFilas": 0, "pagina": 1, "tamanioPagina": 0, "resultados": []}

    # ── Post-procesado de la lista de cambios ──
    for item in resultados.get("resultados", []):
        # 1) traducir tipoCambio
        tipo = item.get("tipoCambio")
        if tipo in TIPO_CAMBIO_MAP:
            item["tipoCambioDesc"] = TIPO_CAMBIO_MAP[tipo]

        # 2) traducir la lista de códigos de cambios
        if isinstance(item.get("cambio"), list):
            item["cambioDesc"] = [
                CAMBIOS_MAP.get(code, code) for code in item["cambio"]
            ]

        # 3) parsear y formatear la fecha usando vuestro helper y luego dateutil
        raw = item.get("fecha")
        # obtenemos la ISO (si es ms UNIX o cadena parseable)
        iso = cima._parse_fecha(raw)
        if isinstance(iso, str):
            try:
                # isoparse convierte un ISO–8601 en datetime
                dt = date_parser.isoparse(iso)
                item["fechaStr"] = dt.strftime("%d/%m/%Y %H:%M:%S")
            except (ValueError, date_parser.ParserError):
                item["fechaStr"] = None
        else:
            item["fechaStr"] = None

    # Construcción de parámetros para metadata
    parametros = {
        k: v
        for k, v in {
            "fecha": fecha,
            "nregistro": nregistro,
            "metodo": metodo,
        }.items()
        if v is not None
    }
    metadatos = _build_metadata(parametros)

    return format_response(resultados, metadatos)

# ---------------------------------------------------------------------------
# 8 · Problemas de suministro
# ---------------------------------------------------------------------------
@app.get(
    "/problemas-suministro",
    operation_id="problemas_suministro",
    summary="Consultar problemas de suministro por uno o varios CN",
    description=constant.problemas_suministro_description,
    response_model=Dict[str, Any],
)
async def problemas_suministro(
    cn: Optional[List[str]] = Query(
        None,
        description="Uno o más Códigos Nacionales. Repetir parámetro: ?cn=123&cn=456"
    )
) -> Dict[str, Any]:
    # Metadatos
    parametros = {"cn": cn}
    metadatos = _build_metadata(parametros, API_PSUM_VERSION)
    metadatos["metadata"]["tipo_problema_suministros"] = cima.TIPOS_PROBLEMA

    # 1) Sin filtro: listado global
    if cn is None:
        listado = await safe_cima_call(cima.psuministro, None)
        data = listado.get("resultados", [])
        return {"data": data, "metadata": metadatos["metadata"]}

    # 2) Con filtro: detalle concurrente
    tareas = [safe_cima_call(cima.psuministro, codigo) for codigo in cn]
    respuestas = await asyncio.gather(*tareas, return_exceptions=True)

    data: Dict[str, Any] = {}
    errors: Dict[str, Any] = {}
    for codigo, resp in zip(cn, respuestas):
        if isinstance(resp, Exception):
            errors[codigo] = {"detail": str(resp)}
        else:
            data[codigo] = resp

    if not data:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "Ningún problema de suministro encontrado",
                "not_found_cn": list(errors.keys()),
                "errors": errors
            }
        )

    result = {"data": data, "metadata": metadatos["metadata"]}
    if errors:
        result["errors"] = errors
    return result

# ---------------------------------------------------------------------------
# 8 · Documentos segmentados – Secciones
# ---------------------------------------------------------------------------
class Seccion(BaseModel):
    seccion: str
    titulo: str
    orden: int

@app.get(
    "/doc-secciones/{tipo_doc}",
    operation_id="doc_secciones",
    summary="Metadatos de secciones de Ficha Técnica/prospecto",
    description=constant.doc_secciones_description,
    response_model=List[Seccion],
)
async def doc_secciones(
    tipo_doc: int = FPath(
        ..., ge=1, le=4,
        description="Tipo de documento (1=FT,2=Prospecto,3-4 otros)."
    ),
    nregistro: Optional[str] = Query(
        None, description="Número de registro del medicamento."
    ),
    cn: Optional[str] = Query(
        None, description="Código Nacional del medicamento."
    ),
) -> Dict[str, Any]:
    if not (nregistro or cn):
        raise HTTPException(status_code=400, detail="Se requiere 'nregistro' o 'cn'.")

    # Llamada segura a la API externa
    try:
        resultados = await safe_cima_call(
            cima.doc_secciones,
            tipo_doc,
            nregistro=nregistro,
            cn=cn
        )
    except Exception as e:
        # Aquí capturas cualquier ValueError, TimeoutError, HTTPError…
        logger.exception("Error llamando a CIMA para doc_secciones")
        # Devolvemos un 502 Bad Gateway con detalle
        raise HTTPException(status_code=502, detail=f"Error al obtener secciones: {e}")

    # Construir metadatos y formatear respuesta
    parametros = {"tipo_doc": tipo_doc, "nregistro": nregistro, "cn": cn}
    metadatos = _build_metadata(parametros)
    return format_response(resultados, metadatos)

# ---------------------------------------------------------------------------
# 9 · Documentos segmentados – Contenido (CORREGIDO)
# ---------------------------------------------------------------------------
class Format(str, Enum):
    json = "json"
    html = "html"
    txt  = "txt"

@app.get(
    "/doc-contenido/{tipo_doc}",
    operation_id="doc_contenido",
    summary="Contenido de secciones de Ficha Técnica/prospecto",
    description=constant.doc_contenido_description,
    response_model=Dict[str, Any],
    responses={
        200: {
            "content": {
                "application/json": {},
                "text/html":        {},
                "text/plain":       {},
            }
        }
    }
)
async def doc_contenido(
    tipo_doc: int = FPath(..., ge=1, le=2),  # ✅ CORREGIDO: Solo 1-2 según documentación
    nregistro: str | None = Query(None),
    cn:        str | None = Query(None),
    seccion:   str | None = Query(None),
    format:    Format      = Query(Format.json, description="Formato: json, html o txt"),
) -> Any:
    if not (nregistro or cn):
        raise HTTPException(400, "Se requiere 'nregistro' o 'cn'.")

    # Llamamos al cliente corregido
    try:
        resultado = await safe_cima_call(
            cima.doc_contenido,
            tipo_doc=tipo_doc,
            nregistro=nregistro,
            cn=cn,
            seccion=seccion,
            format=format.value,
        )
    except Exception as e:
        # ✅ AÑADIDO: Más información del error para debugging
        print(f"Error detallado: {type(e).__name__}: {e}")
        raise HTTPException(502, f"Error al obtener contenido: {e}")

    # Devolvemos tal cual: JSON validado, o HTML/txt crudo
    media_type = {
        Format.json: "application/json",
        Format.html: "text/html",
        Format.txt:  "text/plain",
    }[format]

    if format is Format.json:
        return format_response(resultado, _build_metadata({
            "tipo_doc": tipo_doc,
            "nregistro": nregistro,
            "cn": cn,
            "seccion": seccion,
        }))
    else:
        return Response(content=resultado, media_type=media_type)

# ---------------------------------------------------------------------------
# 10 · Notas de seguridad (unificado)
# ---------------------------------------------------------------------------
@app.get(
    "/notas",
    operation_id="listar_notas",
    summary="Listado de notas de seguridad para uno o varios registros",
    description=constant.listar_notas_description,
    response_model=Dict[str, Any],
)
async def listar_notas(nregistro: List[str] = Query(...)) -> Dict[str, Any]:
    if not nregistro:
        raise HTTPException(400, "…")
    resultados = {}
    errores = {}
    for nr in nregistro:
        try:
            data = await safe_cima_call(cima.notas, nregistro=nr)
            if data:
                resultados[nr] = data
            else:
                errores[nr] = "sin notas"
        except Exception as e:
            errores[nr] = str(e)
    if not resultados:
        raise HTTPException(404, {"error": "ninguna nota", "detalles": errores})
    metadatos = _build_metadata({"nregistro": nregistro})
    return format_response({"notas": resultados, "errores": errores}, metadatos)

@app.get(
    "/notas/{nregistros}",
    operation_id="obtener_notas",
    summary="Detalle de notas de seguridad de uno o varios registros",
    description=constant.obtener_notas_description,
    response_model=Dict[str, Any],
)
async def obtener_notas(
    nregistros: str = FPath(
        ...,
        description="Número(s) de registro. Si son varios, sepáralos con comas: AAA,BBB,CCC"
    )
) -> Dict[str, Any]:
    # 1) Separar en lista
    registros = [nr.strip() for nr in nregistros.split(",") if nr.strip()]

    resultados: Dict[str, Any] = {}
    errores: Dict[str, str] = {}

    # 2) Llamar uno a uno al cliente
    for nr in registros:
        try:
            data = await safe_cima_call(cima.notas, nregistro=nr)
            empty = (
                data is None
                or (isinstance(data, list) and not data)
                or (isinstance(data, dict) and not data)
            )
            if empty:
                errores[nr] = "sin notas"
            else:
                resultados[nr] = data
        except Exception as e:
            errores[nr] = str(e)

    # 3) Si no hay resultados válidos, 404
    if not resultados:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "Ninguna nota encontrada",
                "not_found_nregistro": registros,
                "errores": errores,
            }
        )

    # 4) Formatear la respuesta
    metadatos = _build_metadata({"nregistro": registros})
    # Podemos devolver la misma estructura que usamos en listar_notas:
    payload = {"notas": resultados, "errores": errores}
    return format_response(payload, metadatos)

# ---------------------------------------------------------------------------
# 11 · Materiales informativos (unificado)
# ---------------------------------------------------------------------------
@app.get(
    "/materiales",
    operation_id="listar_materiales",
    summary="Listado de materiales informativos para uno o varios registros",
    description=constant.listar_materiales_description,
    response_model=Dict[str, Any],
)
async def listar_materiales(
    nregistro: List[str] = Query(
        ..., description="Repite el parámetro: ?nregistro=AAA&nregistro=BBB"
    )
) -> Dict[str, Any]:
    if not nregistro:
        raise HTTPException(status_code=400, detail="Se requiere al menos un 'nregistro'.")

    # 1. Crea una tarea por registro
    tareas = [
        safe_cima_call(cima.materiales, nregistro=nr)
        for nr in nregistro
    ]
    # 2. Ejecuta en paralelo y recoge respuestas
    respuestas = await asyncio.gather(*tareas, return_exceptions=True)

    # 3. Filtra errores y None
    data = []
    for nr, res in zip(nregistro, respuestas):
        if isinstance(res, Exception):
            # opcional: loguear o mapear a HTTPException
            continue
        if res:  # aquí res es {"nregistro": nr, "materiales": […]}
            data.append(res)

    if not data:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "Ningún material asociado",
                "not_found_nregistro": nregistro
            }
        )

    # 4. Monta metadatos
    metadatos = _build_metadata({"nregistro": nregistro})

    # 5. Devuelve un dict con data+meta (cumple Dict[str,Any])
    return {
        "data": data,
        "meta": metadatos
    }

@app.get(
    "/materiales/{nregistro}",
    operation_id="obtener_materiales",
    summary="Detalle de materiales informativos de un registro",
    response_model=Dict[str, Any],
)
async def obtener_materiales(
    nregistro: str = FPath(..., description="Número de registro")
) -> Dict[str, Any]:
    try:
        resultado = await safe_cima_call(cima.materiales, nregistro=nregistro)
    except Exception as e:
        logger.error("Error llamando a CIMA para obtener material %s: %s", nregistro, e, exc_info=True)
        raise HTTPException(status_code=502, detail="Error al consultar material en CIMA.")

    if not resultado:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "Ningún material asociado",
                "not_found_nregistro": [nregistro]
            }
        )

    # Aquí NO desempaquetamos resultado["materiales"], 
    # devolvemos el dict completo {nregistro, materiales}
    metadatos = _build_metadata({"nregistro": nregistro})
    return format_response(resultado, metadatos)

# ---------------------------------------------------------------------------
# 12a · HTML completo de ficha técnica (unificado)
# ---------------------------------------------------------------------------
@app.get(
    "/doc-html/ft",
    operation_id="html_ficha_tecnica_multiple",
    summary="Descarga ZIP de fichas técnicas para varios registros",
    description=constant.html_ft_multiple_description,
    response_model=None,
)
async def html_ficha_tecnica_multiple(
    nregistro: List[str] = Query(..., description="Nº de registro (repetir)"),
    filename: str = Query(..., description="Nombre de archivo HTML ('FichaTecnica.html')")
):
    if len(nregistro) == 1:
        try:
            data = await cima.get_html_bytes(
                tipo="ft",
                nregistro=nregistro[0],
                filename=filename
            )
        except HTTPStatusError as e:
            if e.response.status_code == 404:
                raise HTTPException(404, "Ficha técnica no encontrada")
            raise HTTPException(502, str(e))
        return HTMLResponse(content=data)

    return await _html_multiple_zip(
        tipo="ft",
        registros=nregistro,
        filename=filename,
        status_no_content=404
    )

@app.get(
    "/doc-html/ft/{nregistro}/{filename:path}",
    operation_id="html_ficha_tecnica",
    summary="HTML completo de ficha técnica (único registro)",
    description=constant.html_ft_description,
    response_model=None,
)
async def html_ficha_tecnica(
    nregistro: str = FPath(..., description="Número de registro"),
    filename: str = FPath(
        ...,
        description="Ruta y nombre de archivo HTML ('FichaTecnica.html' o '1/FichaTecnica.html')"
    )
):
    try:
        # filename puede ser, p.ej., "FichaTecnica.html" o "1/FichaTecnica.html"
        data = await cima.get_html_bytes(tipo="ft", nregistro=nregistro, filename=filename)
    except HTTPStatusError as e:
        if e.response.status_code == 404:
            raise HTTPException(
                status_code=404,
                detail=f"Ficha técnica {nregistro} sección '{filename}' no encontrada"
            )
        raise HTTPException(
            status_code=502,
            detail=f"Error al obtener HTML de ficha técnica: {e}"
        )
    return HTMLResponse(content=data)

# ---------------------------------------------------------------------------
# 12b · HTML completo de prospecto (unificado)
# ---------------------------------------------------------------------------
@app.get(
    "/doc-html/p",
    operation_id="html_prospecto_multiple",
    summary="Descarga ZIP de prospectos para varios registros",
    description=constant.html_p_multiple_description,
    response_model=None,
)
async def html_prospecto_multiple(
    nregistro: List[str] = Query(..., description="Nº de registro (repetir)"),
    filename: str = Query(..., description="Nombre de archivo HTML ('Prospecto.html')")
):
    if not nregistro or not filename:
        raise HTTPException(status_code=400, detail="Se requiere al menos un 'nregistro' y un 'filename'.")

    if len(nregistro) == 1:
        try:
            # usamos la versión que devuelve bytes completos
            data = await cima.get_html_bytes(tipo="p", nregistro=nregistro[0], filename=filename)
        except HTTPStatusError as e:
            if e.response.status_code == 404:
                raise HTTPException(404, "Prospecto no encontrado")
            raise HTTPException(status_code=502, detail=f"Error al obtener HTML de prospecto: {e}")
        return HTMLResponse(content=data)

    # varios registros → ZIP (igual que antes)
    return await _html_multiple_zip(
        tipo="p",
        registros=nregistro,
        filename=filename,
        status_no_content=404
    )

@app.get(
    "/doc-html/p/{nregistro}/{filename:path}",
    operation_id="html_prospecto",
    summary="HTML completo de prospecto (único registro)",
    description=constant.html_p_description,
    response_model=None,
)
async def html_prospecto(
    nregistro: str = FPath(..., description="Número de registro"),
    filename: str = FPath(
        ..., 
        description="Ruta y nombre de archivo HTML ('Prospecto.html' o '2/Prospecto.html')"
    )
):
    try:
        # filename puede ser p.ej. "Prospecto.html" o "2/Prospecto.html"
        data = await cima.get_html_bytes(tipo="p", nregistro=nregistro, filename=filename)
    except HTTPStatusError as e:
        if e.response.status_code == 404:
            raise HTTPException(
                status_code=404,
                detail=f"Prospecto {nregistro} sección '{filename}' no encontrado"
            )
        raise HTTPException(
            status_code=502,
            detail=f"Error al obtener HTML de prospecto: {e}"
        )
    return HTMLResponse(content=data)

# ---------------------------------------------------------------------------
# 12c · Descargar IPT: PDF único o multipart/mixed con varios PDFs
# ---------------------------------------------------------------------------
from secrets import token_hex
from starlette.responses import StreamingResponse

@app.get(
    "/descargar-ipt",
    operation_id="descargar_ipt",
    summary="Descargar IPT para uno o varios CN (PDF suelto o multipart)",
    description=constant.descargar_ipt_description,
)
async def descargar_ipt(
    background_tasks: BackgroundTasks,
    cn: List[str] = Query(..., description="Uno o varios CN (repetible)"),
    timeout: int = 15,
):
    if not cn:
        raise HTTPException(400, "Debe proporcionar al menos un CN")

    temp_root = Path(tempfile.mkdtemp(prefix="ipts_"))
    descargados: dict[str, Path] = {}
    errores: list[str] = []

    def _cleanup(p: Path): shutil.rmtree(p, ignore_errors=True)

    try:
        # Descarga secuencial (evita 429)
        for code in cn:
            try:
                rutas = await cima.download_ipt(
                    cn=code,
                    base_dir=str(temp_root / code),
                    timeout=timeout,
                )
                if rutas:
                    descargados[code] = Path(rutas[0])
                    logger.debug("✅ %s → %s", code, rutas[0])
                else:
                    errores.append(f"{code}: sin IPT")
            except Exception as e:
                errores.append(f"{code}: {type(e).__name__}")
                logger.exception("❌ %s", code)

        if not descargados:
            raise HTTPException(404, "No se encontró ningún IPT")

        background_tasks.add_task(_cleanup, temp_root)

        # --- un único PDF --------------------------------------------------
        if len(descargados) == 1:
            pdf = next(iter(descargados.values()))
            return FileResponse(
                pdf,
                media_type="application/pdf",
                filename=pdf.name,
            )

        # --- varios PDFs → multipart/mixed --------------------------------
        boundary = f"ipts-{token_hex(8)}"

        def _iter_parts():
            for code, path in descargados.items():
                yield f"--{boundary}\r\n".encode()
                yield b"Content-Type: application/pdf\r\n"
                yield f'Content-Disposition: attachment; filename="{code}_{path.name}"\r\n\r\n'.encode()
                yield path.read_bytes()
                yield b"\r\n"
            yield f"--{boundary}--\r\n".encode()

        return StreamingResponse(
            _iter_parts(),
            media_type=f"multipart/mixed; boundary={boundary}",
            headers={"Content-Disposition": 'attachment; filename="ipts.multipart"'},
        )

    except HTTPException:
        _cleanup(temp_root)
        raise
    except Exception as e:
        logger.exception("Fallo inesperado descargar_ipt(): %s", e)
        _cleanup(temp_root)
        raise HTTPException(500, "Error interno en descargar_ipt")

# ---------------------------------------------------------------------------
# 12d · Descargar imágenes
# ---------------------------------------------------------------------------  
@app.get(
    "/descargar-imagenes",
    operation_id="descargar_imagenes",
    summary="Descargar imágenes para uno o varios CN (sola forma farmacéutica y/o caja)",
    description=constant.descargar_imagenes_description
)
async def descargar_imagenes(
    background_tasks: BackgroundTasks,
    cn: list[str] = Query(..., description="Uno o varios CN (repetible)"),
    tipos: list[str] = Query(["formafarmac", "materialas"], description="Tipos a descargar: ‘formafarmac’, ‘materialas’"),
    timeout: int = 15,
):
    if not cn:
        raise HTTPException(400, "Debe proporcionar al menos un CN")

    # Crear carpeta temporal para esta petición
    temp_root = Path(tempfile.mkdtemp(prefix="imgs_"))
    descargados: dict[str, list[Path]] = {}
    errores: list[str] = []

    def _cleanup(path: Path):
        shutil.rmtree(path, ignore_errors=True)

    try:
        # Por cada CN, descargar las imágenes solicitadas
        for code in cn:
            try:
                rutas = await cima.descargar_imagen(
                    cn=code,
                    tipos=tipos,
                    base_dir=str(temp_root / code),
                    timeout=timeout,
                )
                if rutas:
                    # Convertir a Path y almacenar
                    descargados[code] = [Path(r) for r in rutas]
                    logger.debug("✅ %s → %s", code, rutas)
                else:
                    errores.append(f"{code}: sin imágenes")
            except Exception as e:
                errores.append(f"{code}: {type(e).__name__}")
                logger.exception("❌ %s", code)

        if not descargados:
            # Ningún CN produjo imágenes
            raise HTTPException(404, "No se encontró ninguna imagen para los CN proporcionados")

        # Programar limpieza al final de la petición
        background_tasks.add_task(_cleanup, temp_root)

        # Aplana todas las rutas en una sola lista
        todas_rutas = [ruta for rutas in descargados.values() for ruta in rutas]

        # Si solo hay una imagen, la devolvemos directamente
        if len(todas_rutas) == 1:
            solo = todas_rutas[0]
            return FileResponse(
                solo,
                media_type="image/jpeg",
                filename=solo.name,
            )

        # Varias imágenes → multipart/mixed
        boundary = f"imgs-{token_hex(8)}"

        def _iter_parts():
            for code, rutas in descargados.items():
                for ruta in rutas:
                    yield f"--{boundary}\r\n".encode()
                    yield b"Content-Type: image/jpeg\r\n"
                    yield (
                        f'Content-Disposition: attachment; filename="{code}_{ruta.name}"\r\n\r\n'
                    ).encode()
                    yield ruta.read_bytes()
                    yield b"\r\n"
            yield f"--{boundary}--\r\n".encode()

        return StreamingResponse(
            _iter_parts(),
            media_type=f"multipart/mixed; boundary={boundary}",
            headers={"Content-Disposition": 'attachment; filename="imagenes.multipart"'},
        )

    except HTTPException:
        _cleanup(temp_root)
        raise

    except Exception as e:
        logger.exception("Fallo inesperado en descargar_imagenes_endpoint(): %s", e)
        _cleanup(temp_root)
        raise HTTPException(500, "Error interno al descargar imágenes")

# ---------------------------------------------------------------------------
# 13 · Identificar medicamento en Presentaciones.xls
# ---------------------------------------------------------------------------
@app.get(
    "/identificar-medicamento",
    operation_id="identificar_medicamento",
    summary="Identifica hasta 10 presentaciones en base a CN, nregistro o nombre",
    description=constant.identificar_medicamento_description,
    tags=["Presentaciones"],
    response_model=Dict[str, Any],
)
async def identificar_medicamento(
    nregistro:     Optional[str] = Query(None),
    cn:            Optional[str] = Query(None),
    nombre:        Optional[str] = Query(None),
    laboratorio:   Optional[str] = Query(None),
    atc:           Optional[str] = Query(None),
    estado:        Optional[str] = Query(None),
    comercializado: Optional[bool] = Query(None),
    pagina:        int = Query(1, ge=1),
    page_size:     int = Query(10, ge=1, le=100),
) -> Dict[str, Any]:
    df = app.state.df_presentaciones
    filt = df

    if nregistro:
        filt = _filter_exact(filt, "Nº Registro", nregistro)
    if cn:
        filt = _filter_exact(filt, "Cod. Nacional", cn)
    if laboratorio:
        filt = _filter_contains(filt, "Laboratorio", laboratorio)
    if atc:
        filt = _filter_contains(filt, "Cód. ATC", atc)
    if estado:
        filt = _filter_contains(filt, "Estado", estado)
    if comercializado is not None:
        filt = _filter_bool(filt, "¿Comercializado?", comercializado)

    if nombre:
        # 1) normalizamos la consulta
        norm_query   = _normalize(nombre)
        # 2) calculamos la columna auxiliar
        series_norm  = filt["Presentación"].fillna("").apply(_normalize)
        filt['_norm'] = series_norm

        # 3) coincidencias por substring
        substr = filt[filt['_norm'].str.contains(norm_query)]

        # 4) coincidencias fuzzy
        from difflib import get_close_matches
        similares = get_close_matches(
            norm_query,
            series_norm.tolist(),
            n=page_size,
            cutoff=0.7
        )
        fuzzy = filt[filt['_norm'].isin(similares)]

        # 5) unimos ambos sin duplicados y eliminamos la columna auxiliar
        filt = (
            pd.concat([substr, fuzzy])
              .drop_duplicates()
              .drop(columns=['_norm'])
        )

    total   = len(filt)
    page_df = _paginate(filt, pagina, page_size)
    docs    = page_df.to_dict(orient="records")

    metadatos = _build_metadata({
        "nregistro":      nregistro,
        "cn":             cn,
        "nombre":         nombre,
        "laboratorio":    laboratorio,
        "atc":            atc,
        "estado":         estado,
        "comercializado": comercializado,
        "pagina":         pagina,
        "page_size":      page_size,
        "total":          total,
    })

    return {"data": docs, **metadatos}

# ---------------------------------------------------------------------------
# 14. Nomenclátor de facturación – Búsqueda avanzada
# ---------------------------------------------------------------------------
@app.get(
    "/nomenclator",
    operation_id="buscar_nomenclator",
    summary="Busca productos farmacéuticos en el Nomenclátor de facturación",
    description=constant.nomenclator_description,
    tags=["Nomenclátor"],
    response_model=Dict[str, Any],
)
async def buscar_nomenclator(
    codigo_nacional:           Optional[str]   = Query(None, description="Código Nacional"),
    nombre_producto:           Optional[str]   = Query(None, description="Nombre del producto farmacéutico (parcial, case-insensitive)"),
    tipo_farmaco:              Optional[str]   = Query(None, description="Tipo de fármaco"),
    principio_activo:          Optional[str]   = Query(None, description="Principio activo o asociación"),
    codigo_laboratorio:        Optional[str]   = Query(None, description="Código de laboratorio ofertante"),
    nombre_laboratorio:        Optional[str]   = Query(None, description="Nombre del laboratorio ofertante (parcial)"),
    estado:                    Optional[str]   = Query(None, description="Estado (p.ej. 'ALTA', 'BAJA')"),
    fecha_alta_desde:          Optional[str]   = Query(None, description="Fecha alta ≥ dd/mm/yyyy"),
    fecha_alta_hasta:          Optional[str]   = Query(None, description="Fecha alta ≤ dd/mm/yyyy"),
    fecha_baja_desde:          Optional[str]   = Query(None, description="Fecha baja ≥ dd/mm/yyyy"),
    fecha_baja_hasta:          Optional[str]   = Query(None, description="Fecha baja ≤ dd/mm/yyyy"),
    aportacion_beneficiario:   Optional[str]   = Query(None, description="Aportación del beneficiario"),
    precio_min_iva:            Optional[float] = Query(None, description="Precio venta público mínimo con IVA"),
    precio_max_iva:            Optional[float] = Query(None, description="Precio venta público máximo con IVA"),
    agrupacion_codigo:         Optional[str]   = Query(None, description="Código de agrupación homogénea"),
    agrupacion_nombre:         Optional[str]   = Query(None, description="Nombre de agrupación homogénea (parcial)"),
    diagnostico_hospitalario:  Optional[bool]  = Query(None, description="Diagnóstico hospitalario"),
    larga_duracion:            Optional[bool]  = Query(None, description="Tratamiento de larga duración"),
    especial_control:          Optional[bool]  = Query(None, description="Especial control médico"),
    medicamento_huerfano:      Optional[bool]  = Query(None, description="Medicamento huérfano"),
    pagina:                    int             = Query(1, ge=1, description="Página"),
    page_size:                 int             = Query(10, ge=1, le=100, description="Resultados por página")
) -> Dict[str, Any]:
    df = app.state.df_nomenclator
    filt = df

    # Exact & partial text filters
    if codigo_nacional:
        filt = _filter_exact(filt, "Código Nacional", codigo_nacional)
    if nombre_producto:
        filt = _filter_contains(filt, "Nombre del producto farmacéutico", nombre_producto)
    if tipo_farmaco:
        filt = _filter_contains(filt, "Tipo de fármaco", tipo_farmaco)
    if principio_activo:
        filt = _filter_contains(filt, "Principio activo o asociación de principios activos", principio_activo)
    if codigo_laboratorio:
        filt = _filter_exact(filt, "Código del laboratorio ofertante", codigo_laboratorio)
    if nombre_laboratorio:
        filt = _filter_contains(filt, "Nombre del laboratorio ofertante", nombre_laboratorio)
    if estado:
        filt = _filter_contains(filt, "Estado", estado)
    if aportacion_beneficiario:
        filt = _filter_contains(filt, "Aportación del beneficiario", aportacion_beneficiario)
    if agrupacion_codigo:
        filt = _filter_exact(filt, "Código de la agrupación homogénea del producto sanitario", agrupacion_codigo)
    if agrupacion_nombre:
        filt = _filter_contains(filt, "Nombre de la agrupación homogénea del producto sanitario", agrupacion_nombre)

    # Numeric filters
    filt = _filter_numeric(filt, "Precio venta al público con IVA", precio_min_iva, precio_max_iva)

    # Boolean filters
    for flag, col in [
        (diagnostico_hospitalario, "Diagnóstico hospitalario"),
        (larga_duracion, "Tratamiento de larga duración"),
        (especial_control, "Especial control médico"),
        (medicamento_huerfano, "Medicamento huérfano"),
    ]:
        if flag is not None:
            filt = _filter_bool(filt, col, flag)

    # Date filters
    if fecha_alta_desde:
        filt = _filter_date(filt, "Fecha de alta en el nomenclátor", fecha_alta_desde, 'ge')
    if fecha_alta_hasta:
        filt = _filter_date(filt, "Fecha de alta en el nomenclátor", fecha_alta_hasta, 'le')
    if fecha_baja_desde:
        filt = _filter_date(filt, "Fecha de baja en el nomenclátor", fecha_baja_desde, 'ge')
    if fecha_baja_hasta:
        filt = _filter_date(filt, "Fecha de baja en el nomenclátor", fecha_baja_hasta, 'le')

    total = len(filt)
    page_df = _paginate(filt, pagina, page_size)
    records = page_df.to_dict(orient="records")

    metadatos = _build_metadata({
        "codigo_nacional":         codigo_nacional,
        "nombre_producto":         nombre_producto,
        "tipo_farmaco":            tipo_farmaco,
        "principio_activo":        principio_activo,
        "codigo_laboratorio":      codigo_laboratorio,
        "nombre_laboratorio":      nombre_laboratorio,
        "estado":                  estado,
        "fecha_alta_desde":        fecha_alta_desde,
        "fecha_alta_hasta":        fecha_alta_hasta,
        "fecha_baja_desde":        fecha_baja_desde,
        "fecha_baja_hasta":        fecha_baja_hasta,
        "aportacion_beneficiario": aportacion_beneficiario,
        "precio_min_iva":          precio_min_iva,
        "precio_max_iva":          precio_max_iva,
        "agrupacion_codigo":       agrupacion_codigo,
        "agrupacion_nombre":       agrupacion_nombre,
        "diagnostico_hospitalario":diagnostico_hospitalario,
        "larga_duracion":          larga_duracion,
        "especial_control":        especial_control,
        "medicamento_huerfano":    medicamento_huerfano,
        "pagina":                  pagina,
        "page_size":               page_size,
        "total":                   total,
    })

    return {"data": records, **metadatos}

@app.get(
    "/system-info-prompt",
    operation_id="get_system_info_prompt",
    summary="Obtener el Prompt del sistema para el agente MCP",
    description=constant.system_info_prompt_description
)
async def get_system_prompt() -> str:
    return constant.MCP_AEMPS_SYSTEM_PROMPT

# ---------------------------------------------------------------------------
#   Inicializar MCP
# ---------------------------------------------------------------------------
mcp = FastApiMCP(
    app,
    name="AEMPS CIMA MCP",
    description="Acceso estructurado en tiempo real a datos regulatorios"
)

# Montamos las rutas MCP en /mcp (o el prefijo que configure FastApiMCP)
mcp.mount()
# mcp.setup_server()
