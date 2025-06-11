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
import pandas as pd
import asyncio
from datetime import datetime, timezone
from pathlib import Path
import logging
from typing import Any, List, Optional, Literal, AsyncIterator, Tuple, Dict
from httpx import HTTPStatusError
from fastapi import Body, FastAPI, HTTPException, Query, Depends, Request, WebSocket, Path as FPath
from fastapi_mcp import FastApiMCP
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials, OAuth2PasswordBearer
from fastapi.responses import JSONResponse, StreamingResponse
import zipfile
from io import BytesIO
import io
from aiohttp.client_exceptions import ClientResponseError
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
                         _filter_numeric, format_response, _normalize,
                         API_CIMA_AEMPS_VERSION, API_PSUM_VERSION)

# ------------------------------------------------------------
# 1) Configuración global de logging
# ------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s"
)
logger = logging.getLogger("mcp_aemps_server")

# ---------------------------------------------------------------------------
#   Crear la aplicación FastAPI + MCP
# ---------------------------------------------------------------------------
app = FastAPI(
    title="AEMPS CIMA MCP",
    version="0.1.0",
    description="Herramientas MCP sobre la API CIMA de la AEMPS",
    openapi_url="/openapi.json",
    docs_url="/docs",
    redoc_url=None,
    lifespan=lifespan,
    router_dependencies=[Depends(RateLimiter(times=20, seconds=60))],
    swagger_ui_parameters={"defaultModelsExpandDepth": -1},
)

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
#   Middleware adicional (cabeceras de seguridad)
# ---------------------------------------------------------------------------
@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.update({
        "X-Frame-Options": "DENY",
        "X-Content-Type-Options": "nosniff",
        # "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
        # "Content-Security-Policy": "default-src 'self'"
    })
    return response


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
mcp.setup_server()


# ---------------------------------------------------------------------------
#   Health & Observability
# ---------------------------------------------------------------------------
@app.get('/health', include_in_schema=False)
async def health():
    return JSONResponse({'status': 'ok'})


Instrumentator().instrument(app).expose(app)
FastAPIInstrumentor.instrument_app(app)


# ---------------------------------------------------------------------------
# 1 · Medicamento (ficha única)
# ---------------------------------------------------------------------------
@app.get(
    "/medicamento",
    operation_id="obtener_medicamento",
    summary="Obtener ficha completa de un medicamento (por CN o nº de registro)",
    description=constant.medicamento_description,
    response_model=Dict[str, Any],  # Mejor definir un modelo Pydantic específico
)
@cache(expire=3600, key_builder=lambda func, *args, **kwargs: f"medicamento:{kwargs.get('cn', '')}:{kwargs.get('nregistro', '')}")
async def obtener_medicamento(
    cn: Optional[str] = Query(
        None,
        description="Código Nacional (CN) del medicamento. Ejemplo: '654321'.",
        regex=r'^\d+$'
    ),
    nregistro: Optional[str] = Query(
        None,
        description="Número de registro AEMPS. Ejemplo: '00123'.",
        regex=r'^\d+$'
    ),
) -> Dict[str, Any]:
    # Validación de entrada
    if not (cn or nregistro):
        raise HTTPException(
            status_code=400,
            detail={
                "error": "Parámetros insuficientes",
                "message": "Debe indicar al menos uno de los parámetros: 'cn' o 'nregistro'.",
                "required_params": ["cn", "nregistro"]
            }
        )

    cn_clean = cn.strip() if cn else None
    nregistro_clean = nregistro.strip() if nregistro else None

    try:
        logger.info(f"Consultando medicamento - CN: {cn_clean}, NRegistro: {nregistro_clean}")
        resultado = await cima.medicamento(cn=cn_clean, nregistro=nregistro_clean)

        # Verificar si se encontró el medicamento
        if not resultado or (isinstance(resultado, dict) and not resultado.get("data")):
            raise HTTPException(
                status_code=404,
                detail={
                    "error": "Medicamento no encontrado",
                    "message": f"No se encontró medicamento con CN: {cn_clean} o NRegistro: {nregistro_clean}",
                    "search_params": {"cn": cn_clean, "nregistro": nregistro_clean}
                }
            )

        # Construir metadatos usando la función auxiliar
        parametros = {"cn": cn_clean, "nregistro": nregistro_clean}
        metadatos = _build_metadata(parametros)

        respuesta = format_response(resultado, metadatos)

        return respuesta

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error inesperado al consultar medicamento: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={
                "error": "Error interno del servidor",
                "message": "Error al consultar el servicio CIMA",
                "support": "Contacte con el administrador si el problema persiste"
            }
        )

# ---------------------------------------------------------------------------
# 2 · Medicamentos (listado con filtros) — Metadata adaptada
# ---------------------------------------------------------------------------
@app.get(
    "/medicamentos",
    operation_id="buscar_medicamentos",
    summary="Listado de medicamentos con filtros regulatorios avanzados",
    description=constant.medicamentos_description,
    response_model=Dict[str, Any],
)
async def buscar_medicamentos(
    nombre: Optional[str] = Query(
        None,
        description="Nombre del medicamento (coincidencia parcial o exacta)."
    ),
    laboratorio: Optional[str] = Query(
        None,
        description="Nombre del laboratorio fabricante."
    ),
    practiv1: Optional[str] = Query(
        None,
        description="Nombre del principio activo principal."
    ),
    practiv2: Optional[str] = Query(
        None,
        description="Nombre de un segundo principio activo."
    ),
    idpractiv1: Optional[str] = Query(
        None,
        description="ID numérico del principio activo principal."
    ),
    idpractiv2: Optional[str] = Query(
        None,
        description="ID numérico de un segundo principio activo."
    ),
    cn: Optional[str] = Query(
        None,
        description="Código Nacional del medicamento."
    ),
    atc: Optional[str] = Query(
        None,
        description="Código ATC o descripción parcial del mismo."
    ),
    nregistro: Optional[str] = Query(
        None,
        description="Número de registro AEMPS."
    ),
    npactiv: Optional[int] = Query(
        None,
        description="Número de principios activos asociados al medicamento."
    ),
    triangulo: Optional[int] = Query(
        None,
        ge=0,
        le=1,
        description="1 = Tienen triángulo, 0 = No tienen triángulo."
    ),
    huerfano: Optional[int] = Query(
        None,
        ge=0,
        le=1,
        description="1 = Huérfano, 0 = No huérfano."
    ),
    biosimilar: Optional[int] = Query(
        None,
        ge=0,
        le=1,
        description="1 = Biosimilar, 0 = No biosimilar."
    ),
    sust: Optional[int] = Query(
        None,
        ge=1,
        le=5,
        description="""
        Tipo de medicamento especial (1 a 5 según clasificación):
        1 – Biológicos, 2 – Medicamentos con principios activos de
        estrecho margen terapéutico, 3 – Medicamentos de especial
        control médico o con medidas especiales de seguridad, 4 –
        Medicamentos para el aparato respiratorio administrados por vía
        inhalatoria, 5 – Medicamentos de estrecho margen terapéutico
        """
    ),
    vmp: Optional[str] = Query(
        None,
        description="ID del código VMP para buscar equivalentes clínicos."
    ),
    comerc: Optional[int] = Query(
        None,
        ge=0,
        le=1,
        description="1 = Comercializados, 0 = No comercializados."
    ),
    autorizados: Optional[int] = Query(
        None,
        ge=0,
        le=1,
        description="1 = Solo autorizados, 0 = Solo no autorizados."
    ),
    receta: Optional[int] = Query(
        None,
        ge=0,
        le=1,
        description="1 = Con receta, 0 = Sin receta."
    ),
    estupefaciente: Optional[int] = Query(
        None,
        ge=0,
        le=1,
        description="1 = Incluye estupefacientes, 0 = Excluye."
    ),
    psicotropo: Optional[int] = Query(
        None,
        ge=0,
        le=1,
        description="1 = Incluye psicótropos, 0 = Excluye."
    ),
    estuopsico: Optional[int] = Query(
        None,
        ge=0,
        le=1,
        description="1 = Incluye estupefacientes o psicótropos, 0 = Excluye."
    ),
    pagina: Optional[int] = Query(
        1,
        ge=1,
        description="Número de página de resultados (mínimo 1)."
    ),
) -> Dict[str, Any]:
    resultados = await cima.medicamentos(**locals())

    # Construir metadatos usando la función auxiliar
    parametros = {
        "nombre": nombre,
        "laboratorio": laboratorio,
        "practiv1": practiv1,
        "practiv2": practiv2,
        "idpractiv1": idpractiv1,
        "idpractiv2": idpractiv2,
        "cn": cn,
        "atc": atc,
        "nregistro": nregistro,
        "npactiv": npactiv,
        "triangulo": triangulo,
        "huerfano": huerfano,
        "biosimilar": biosimilar,
        "sust": sust,
        "vmp": vmp,
        "comerc": comerc,
        "autorizados": autorizados,
        "receta": receta,
        "estupefaciente": estupefaciente,
        "psicotropo": psicotropo,
        "estuopsico": estuopsico,
        "pagina": pagina,
    }
    metadatos = _build_metadata(parametros)

    respuesta = format_response(resultados, metadatos)

    return respuesta


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
    reglas: List[dict[str, Any]] = Body(
        ...,
        description="Lista de reglas con {seccion, texto, contiene}. Cada regla debe incluir: 'seccion' en formato 'N' o 'N.N', 'texto' (cadena) y 'contiene' (0 o 1)."
    ),
) -> Dict[str, Any]:
    # Validación de reglas
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

    resultados = await cima.buscar_en_ficha_tecnica(reglas)

    # Construir metadatos usando la función auxiliar
    parametros = {"reglas": reglas}
    metadatos = _build_metadata(parametros)

    respuesta = format_response(resultados, metadatos)

    return respuesta

# ---------------------------------------------------------------------------
# 4 · Presentaciones (listado + detalle) — Metadata adaptada
# ---------------------------------------------------------------------------
@app.get(
    "/presentaciones",
    operation_id="listar_presentaciones",
    summary="Listar presentaciones de un medicamento con filtros (cn, nregistro, etc.)",
    description=constant.presentaciones_description,
    response_model=Dict[str, Any],  # Mejor definir un modelo Pydantic específico si se desea
)
async def listar_presentaciones(
    cn: Optional[str] = Query(
        None,
        description="Código Nacional del medicamento."
    ),
    nregistro: Optional[str] = Query(
        None,
        description="Número de registro AEMPS."
    ),
    vmp: Optional[str] = Query(
        None,
        description="ID del código VMP para equivalentes clínicos."
    ),
    vmpp: Optional[str] = Query(
        None,
        description="ID del código VMPP."
    ),
    idpractiv1: Optional[str] = Query(
        None,
        description="ID del principio activo."
    ),
    comerc: Optional[int] = Query(
        None,
        ge=0,
        le=1,
        description="1 = Comercializados, 0 = No comercializados."
    ),
    estupefaciente: Optional[int] = Query(
        None,
        ge=0,
        le=1,
        description="1 = Incluye estupefacientes, 0 = Excluye."
    ),
    psicotropo: Optional[int] = Query(
        None,
        ge=0,
        le=1,
        description="1 = Incluye psicótropos, 0 = Excluye."
    ),
    estuopsico: Optional[int] = Query(
        None,
        ge=0,
        le=1,
        description="1 = Incluye estupefacientes o psicótropos, 0 = Excluye."
    ),
    pagina: Optional[int] = Query(
        1,
        ge=1,
        description="Número de página de resultados (mínimo 1)."
    ),
) -> Dict[str, Any]:
    resultados = await cima.presentaciones(**locals())

    # Construir metadata usando la función auxiliar
    parametros = {
        "cn": cn,
        "nregistro": nregistro,
        "vmp": vmp,
        "vmpp": vmpp,
        "idpractiv1": idpractiv1,
        "comerc": comerc,
        "estupefaciente": estupefaciente,
        "psicotropo": psicotropo,
        "estuopsico": estuopsico,
        "pagina": pagina,
    }
    metadatos = _build_metadata(parametros)

    respuesta = format_response(resultados, metadatos)

    return respuesta

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
        description="Uno o varios Códigos Nacionales. Repetir el parámetro: ?cn=765432&cn=654321"
    )
) -> Dict[str, Any]:
    if not cn:
        raise HTTPException(status_code=400, detail="Debe indicar al menos un 'cn'.")

    # caso único sin cambios
    if len(cn) == 1:
        detalle = await cima.presentacion(cn[0])
        metadatos = _build_metadata({"cn": cn[0]})
        if isinstance(detalle, dict):
            return {**detalle, **metadatos}
        return {"data": detalle, **metadatos}

    # múltiples: parallel con manejo de excepciones
    tasks = [cima.presentacion(c) for c in cn]
    respuestas = await asyncio.gather(*tasks, return_exceptions=True)

    result_dict: Dict[str, Any] = {}
    errors: Dict[str, Any] = {}

    for codigo, resp in zip(cn, respuestas):
        if isinstance(resp, Exception):
            # si viene de HTTPStatusError con código 404, lo marcamos pero no lo incluimos en result_dict
            if isinstance(resp, HTTPStatusError) and resp.response.status_code == 404:
                errors[codigo] = {"status_code": 404, "detail": "No encontrado"}
            else:
                errors[codigo] = {
                    "status_code": getattr(resp, "response", {}).get("status_code", 502),
                    "detail": str(resp)
                }
            continue

        # éxito
        metadatos = _build_metadata({"cn": codigo})
        if isinstance(resp, dict):
            result_dict[codigo] = {**resp, **metadatos}
        else:
            result_dict[codigo] = {"data": resp, **metadatos}

    # si todos fallaron, devolvemos un 404
    if not result_dict:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "Ninguna presentación encontrada",
                "not_found_cn": list(errors.keys()),
                "errors": errors
            }
        )

    # devolvemos los parciales y el mapa de errores
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
    practiv1: Optional[str] = Query(None, description="Nombre del principio activo principal."),
    idpractiv1: Optional[str] = Query(None, description="ID del principio activo principal."),
    dosis: Optional[str] = Query(None, description="Dosis del medicamento."),
    forma: Optional[str] = Query(None, description="Nombre de la forma farmacéutica."),
    atc: Optional[str] = Query(None, description="Código ATC o descripción parcial."),
    nombre: Optional[str] = Query(None, description="Nombre del medicamento."),
    modoArbol: Optional[int] = Query(None, description="Si se indica, devuelve resultados en modo jerárquico."),
    pagina: Optional[int] = Query(1, ge=1, description="Número de página (si la API lo soporta)."),
) -> Dict[str, Any]:
    resultados = await safe_cima_call(cima.vmpp, **locals())

    parametros = {k: v for k, v in {
        "practiv1": practiv1,
        "idpractiv1": idpractiv1,
        "dosis": dosis,
        "forma": forma,
        "atc": atc,
        "nombre": nombre,
        "modoArbol": modoArbol,
        "pagina": pagina,
    }.items() if v is not None}
    metadatos = _build_metadata(parametros)

    respuesta = format_response(resultados, metadatos)

    return respuesta


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
@app.get(
    "/registro-cambios",
    operation_id="registro_cambios",
    summary="Historial de altas, bajas y modificaciones de medicamentos",
    description=constant.registro_cambios_description,
    response_model=Dict[str, Any],
)
async def registro_cambios(
    fecha: Optional[str] = Query(None, description="Fecha (dd/mm/yyyy)."),
    nregistro: Optional[str] = Query(None, description="Número de registro AEMPS."),
    metodo: str = Query("GET", regex="^(GET|POST)$", description="Método HTTP interno."),
) -> Dict[str, Any]:
    resultados = await safe_cima_call(
        cima.registro_cambios,
        fecha=fecha,
        nregistro=nregistro,
        metodo=metodo
    )

    parametros = {k: v for k, v in {
        "fecha": fecha,
        "nregistro": nregistro,
        "metodo": metodo,
    }.items() if v is not None}
    metadatos = _build_metadata(parametros)

    respuesta = format_response(resultados, metadatos)

    return respuesta


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
    parametros = {"cn": cn}
    metadatos = _build_metadata(parametros)
    tipo_problema_suministros = {
        1: "Consultar Nota Informativa",
        2: "Suministro solo a hospitales",
        3: "El médico prescriptor deberá determinar la posibilidad de utilizar otros tratamientos comercializados",
        4: "Desabastecimiento temporal",
        5: "Existe/n otro/s medicamento/s con el mismo principio activo y para la misma vía de administración",
        6: "Existe/n otro/s medicamento/s con los mismos principios activos y para la misma vía de administración",
        7: "Se puede solicitar como medicamento extranjero",
        8: "Se recomienda restringir su prescripción reservándolo para casos en que no exista una alternativa apropiada",
        9: "El titular de autorización de comercialización está realizando una distribución controlada al existir unidades limitadas"
    }
    metadatos["metadata"]["tipo_problema_suministros"] = tipo_problema_suministros

    if cn is None:
        resultado = await cima.psuministro(None)
        if isinstance(resultado, dict):
            return {**resultado, **metadatos}
        return {"data": resultado, **metadatos}

    tasks = [cima.psuministro(c) for c in cn]
    respuestas = await asyncio.gather(*tasks, return_exceptions=True)

    result_dict: Dict[str, Any] = {}
    errors: Dict[str, Any] = {}

    for codigo, resp in zip(cn, respuestas):
        if isinstance(resp, Exception):
            errors[codigo] = {"detail": str(resp)}
            continue

        if isinstance(resp, dict):
            result_dict[codigo] = {**resp, **metadatos}
        else:
            result_dict[codigo] = {"data": resp, **metadatos}

    if not result_dict:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "Ningún problema de suministro encontrado",
                "not_found_cn": list(errors.keys()),
                "errors": errors
            }
        )

    response = {**result_dict, **metadatos}
    if errors:
        response["errors"] = errors
    return response

# ---------------------------------------------------------------------------
# 9 · Documentos segmentados – Secciones
# ---------------------------------------------------------------------------
@app.get(
    "/doc-secciones/{tipo_doc}",
    operation_id="doc_secciones",
    summary="Metadatos de secciones de Ficha Técnica/prospecto",
    description=constant.doc_secciones_description,
    response_model=Dict[str, Any],
)
async def doc_secciones(
    tipo_doc: int = Path(
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

    resultados = await cima.doc_secciones(tipo_doc, nregistro=nregistro, cn=cn)

    # Construir metadatos usando la función auxiliar
    parametros = {"tipo_doc": tipo_doc, "nregistro": nregistro, "cn": cn}
    metadatos = _build_metadata(parametros)

    respuesta = format_response(resultados, metadatos)

    return respuesta

# ---------------------------------------------------------------------------
# 9 · Documentos segmentados – Contenido
# ---------------------------------------------------------------------------
@app.get(
    "/doc-contenido/{tipo_doc}",
    operation_id="doc_contenido",
    summary="Contenido HTML/JSON de secciones de Ficha Técnica/prospecto",
    description=constant.doc_contenido_description,
    response_model=Dict[str, Any],
)
async def doc_contenido(
    tipo_doc: int = Path(
        ..., ge=1, le=4,
        description="Tipo de documento (1=FT,2=Prospecto,3-4 otros)."
    ),
    nregistro: Optional[str] = Query(
        None, description="Número de registro del medicamento."
    ),
    cn: Optional[str] = Query(
        None, description="Código Nacional del medicamento."
    ),
    seccion: Optional[str] = Query(
        None, description="Sección a obtener, p.ej. '4.2'."
    ),
) -> Dict[str, Any]:
    if not (nregistro or cn):
        raise HTTPException(status_code=400, detail="Se requiere 'nregistro' o 'cn'.")

    resultados = await cima.doc_contenido(tipo_doc, nregistro=nregistro, cn=cn, seccion=seccion)

    # Construir metadatos usando la función auxiliar
    parametros = {"tipo_doc": tipo_doc, "nregistro": nregistro, "cn": cn, "seccion": seccion}
    metadatos = _build_metadata(parametros)

    respuesta = format_response(resultados, metadatos)

    return respuesta


# ---------------------------------------------------------------------------
# 10 · Notas de seguridad
# ---------------------------------------------------------------------------
@app.get(
    "/notas",
    operation_id="listar_notas",
    summary="Listado de notas de seguridad para uno o varios registros",
    description=constant.listar_notas_description,
    response_model=Dict[str, Any],
)
async def listar_notas(
    nregistro: List[str] = Query(
        ..., description="Número(s) de registro. Repetir parámetro: ?nregistro=AAA&nregistro=BBB"
    )
) -> Dict[str, Any]:
    if not nregistro:
        raise HTTPException(status_code=400, detail="Se requiere al menos un 'nregistro'.")

    # caso único sin cambios
    if len(nregistro) == 1:
        resultado = await cima.notas(nregistro=nregistro[0])
        metadatos = _build_metadata({"nregistro": nregistro[0]})
        if isinstance(resultado, list):
            return [{**item, **metadatos} for item in resultado]
        return {"data": resultado, **metadatos}

    tasks = [cima.notas(nregistro=nr) for nr in nregistro]
    respuestas = await asyncio.gather(*tasks, return_exceptions=True)

    result_dict: Dict[str, Any] = {}
    errors: Dict[str, Any] = {}

    for nr, resp in zip(nregistro, respuestas):
        if isinstance(resp, Exception):
            errors[nr] = {"detail": str(resp)}
            continue

        metadatos = _build_metadata({"nregistro": nr})
        if isinstance(resp, list):
            result_dict[nr] = [{**item, **metadatos} for item in resp]
        else:
            result_dict[nr] = {"data": resp, **metadatos}

    if not result_dict:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "Ningún problema de suministro encontrado",
                "not_found_cn": list(errors.keys()),
                "errors": errors
            }
        )

    response = {**result_dict}
    if errors:
        response["errors"] = errors
    return response


# ---------------------------------------------------------------------------
# 10 · Detalle de notas de seguridad
# ---------------------------------------------------------------------------
@app.get(
    "/notas/{nregistro}",
    operation_id="obtener_notas",
    summary="Detalle de notas de seguridad de un registro",
    description=constant.obtener_notas_description,
    response_model=Dict[str, Any],
)
async def obtener_notas(
    nregistro: str = Path(
        ..., description="Número de registro"
    )
) -> Dict[str, Any]:
    resultado = await cima.notas(nregistro=nregistro)
    parametros = {"nregistro": nregistro}
    metadatos = _build_metadata(parametros)

    respuesta = format_response(resultado, metadatos)

    return respuesta

# ---------------------------------------------------------------------------
# 11 · Materiales informativos
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
        ..., description="Número(s) de registro. Repetir parámetro: ?nregistro=AAA&nregistro=BBB"
    )
) -> Dict[str, Any]:
    if not nregistro:
        raise HTTPException(status_code=400, detail="Se requiere al menos un 'nregistro'.")

    # caso único sin cambios
    if len(nregistro) == 1:
        resultado = await cima.materiales(nregistro=nregistro[0])
        metadatos = _build_metadata({"nregistro": nregistro[0]})
        if isinstance(resultado, list):
            return [{**item, **metadatos} for item in resultado]
        return {"data": resultado, **metadatos}

    tasks = [cima.materiales(nregistro=nr) for nr in nregistro]
    respuestas = await asyncio.gather(*tasks, return_exceptions=True)

    result_dict: Dict[str, Any] = {}
    errors: Dict[str, Any] = {}

    for nr, resp in zip(nregistro, respuestas):
        if isinstance(resp, Exception):
            errors[nr] = {"detail": str(resp)}
            continue

        metadatos = _build_metadata({"nregistro": nr})
        if isinstance(resp, list):
            result_dict[nr] = [{**item, **metadatos} for item in resp]
        else:
            result_dict[nr] = {"data": resp, **metadatos}
    
    if not result_dict:
        raise HTTPException(
            status_code=502,
            detail={
                "error": "Ningún problema de suministro encontrado",
                "not_found_cn": list(errors.keys()),
                "errors": errors
            }
        )

    response = {**result_dict}
    if errors:
        response["errors"] = errors
    return response


@app.get(
    "/materiales/{nregistro}",
    operation_id="obtener_materiales",
    summary="Detalle de materiales informativos de un registro",
    description=constant.obtener_materiales_description,
    response_model=Dict[str, Any],
)
async def obtener_materiales(
    nregistro: str = Path(..., description="Número de registro")
) -> Dict[str, Any]:
    resultado = await cima.materiales(nregistro=nregistro)
    # Metadata
    parametros = {"nregistro": nregistro}
    metadatos = _build_metadata(parametros)

    respuesta = format_response(resultado, metadatos)

    return respuesta

# ----------------------------------------------------------------------------
async def _html_multiple_zip(
    tipo: Literal["ft", "p"],
    registros: List[str],
    filename: str,
    status_no_content: int
) -> StreamingResponse:
    html_bytes, errors = await cima._fetch_multiple_bytes(tipo, registros, filename)
    if not html_bytes:
        raise HTTPException(
            status_code=status_no_content,
            detail={"error": "No se pudo generar ningún HTML", "errors": errors}
        )

    # Crear ZIP en memoria
    bio = BytesIO()
    with zipfile.ZipFile(bio, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        # Agregar cada HTML
        for nr, data in html_bytes.items():
            zf.writestr(f"{nr}_{filename}", data)
        # Incluir metadata y errores si hay
        metadata = _build_metadata({"nregistro": registros, "filename": filename})
        zf.writestr("metadata.json", json.dumps(metadata))
        if errors:
            zf.writestr("errors.json", json.dumps(errors))
    bio.seek(0)

    return StreamingResponse(
        bio,
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename=docs_{tipo}.zip"}
    )

# ---------------------------------------------------------------------------
# 12a · HTML completo de ficha técnica
# ---------------------------------------------------------------------------
@app.get(
    "/doc-html/ft",
    operation_id="html_ficha_tecnica_multiple",
    summary="Descarga ZIP de fichas técnicas para varios registros"
)
async def html_ficha_tecnica_multiple(
    nregistro: List[str] = Query(..., description="Nº de registro (repetir)"),
    filename: str = Query(..., description="Nombre de archivo HTML ('FichaTecnica.html')")
) -> StreamingResponse:
    if not nregistro or not filename:
        raise HTTPException(status_code=400, detail="Se requiere al menos un 'nregistro' y un 'filename'.")
    if len(nregistro) == 1:
        content = cima.get_html(tipo="ft", nregistro=nregistro[0], filename=filename)
        return StreamingResponse(content, media_type="text/html")
    return await _html_multiple_zip(tipo="ft", registros=nregistro, filename=filename, status_no_content=502)

@app.get(
    "/doc-html/ft/{nregistro}/{filename}",
    operation_id="html_ficha_tecnica",
    summary="HTML completo de ficha técnica (único registro)"
)
async def html_ficha_tecnica(
    nregistro: str = Path(..., description="Número de registro"),
    filename: str = Path(..., description="Nombre de archivo HTML ('FichaTecnica.html')")
) -> StreamingResponse:
    content = cima.get_html(tipo="ft", nregistro=nregistro, filename=filename)
    return StreamingResponse(content, media_type="text/html")


# ---------------------------------------------------------------------------
# 12b · HTML completo de prospecto
# ---------------------------------------------------------------------------
@app.get(
    "/doc-html/p",
    operation_id="html_prospecto_multiple",
    summary="Descarga ZIP de prospectos para varios registros"
)
async def html_prospecto_multiple(
    nregistro: List[str] = Query(..., description="Nº de registro (repetir)"),
    filename: str = Query(..., description="Nombre de archivo HTML ('Prospecto.html')")
) -> StreamingResponse:
    if not nregistro or not filename:
        raise HTTPException(status_code=400, detail="Se requiere al menos un 'nregistro' y un 'filename'.")
    if len(nregistro) == 1:
        content = cima.get_html(tipo="p", nregistro=nregistro[0], filename=filename)
        return StreamingResponse(content, media_type="text/html")
    return await _html_multiple_zip(tipo="p", registros=nregistro, filename=filename, status_no_content=404)

@app.get(
    "/doc-html/p/{nregistro}/{filename}",
    operation_id="html_prospecto",
    summary="HTML completo de prospecto (único registro)"
)
async def html_prospecto(
    nregistro: str = Path(..., description="Número de registro"),
    filename: str = Path(..., description="Nombre de archivo HTML ('Prospecto.html' o sección específica)")
) -> StreamingResponse:
    content = cima.get_html(tipo="p", nregistro=nregistro, filename=filename)
    return StreamingResponse(content, media_type="text/html")


# ---------------------------------------------------------------------------
# 12c · Descargar Informe de Posicionamiento Terapéutico (IPT)
# ---------------------------------------------------------------------------
@app.get(
    "/descargar-ipt",
    operation_id="descargar_ipt",
    summary="Descargar IPT para uno o varios CN o registros",
    response_model=Dict[str, Any],
)
async def descargar_ipt(
    request: Request,
    cn: Optional[List[str]] = Query(None, description="CN (repetir)"),
    nregistro: Optional[List[str]] = Query(None, description="NRegistro (repetir)"),
    zip: bool = Query(False, description="Si se quiere descargar todo en un ZIP")
) -> Any:
    if not cn and not nregistro:
        raise HTTPException(status_code=400, detail="Debe especificar al menos un 'cn' o un 'nregistro'.")

    # preparativos
    inputs: List[Tuple[str, str]] = []
    tasks: List[Any] = []
    if cn:
        for c in cn:
            inputs.append(("cn", c))
            tasks.append(cima.download_docs(cn=c, nregistro=None, tipos=["ipt"]))
    if nregistro:
        for nr in nregistro:
            inputs.append(("nregistro", nr))
            tasks.append(cima.download_docs(cn=None, nregistro=nr, tipos=["ipt"]))

    respuestas = await asyncio.gather(*tasks, return_exceptions=True)

    data_paths: List[str] = []
    errors: Dict[str, Any] = {}
    for (kind, val), resp in zip(inputs, respuestas):
        if isinstance(resp, Exception):
            errors[f"{kind}={val}"] = {"detail": str(resp)}
            continue
        data_paths.extend(resp)

    if not data_paths:
        raise HTTPException(
            status_code=404,
            detail={"error": "No se descargó ningún IPT", "errors": errors}
        )

    # Convertir rutas locales en URLs accesibles
    base_static_url = request.url_for("data")  # e.g. http://host/data
    urls = []
    for path in data_paths:
        # asumimos que 'path' empieza por 'data/'
        rel = Path(path).relative_to("data")
        urls.append(f"{base_static_url}/{rel.as_posix()}")

    # Si piden ZIP, lo creamos en memoria
    if zip:
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w") as zf:
            for file_path in data_paths:
                fn = Path(file_path).name
                zf.write(file_path, arcname=fn)
        zip_buffer.seek(0)
        return StreamingResponse(
            zip_buffer,
            media_type="application/x-zip-compressed",
            headers={"Content-Disposition": 'attachment; filename="ipts.zip"'}
        )

    # Devolvemos JSON con URLs
    response = {"urls": urls}
    if errors:
        response["errors"] = errors
    return JSONResponse(response)

# ---------------------------------------------------------------------------
# 13 · Identificar medicamento en Presentaciones.xls
# ---------------------------------------------------------------------------
@app.get(
    "/identificar-medicamento",
    operation_id="identificar_medicamento",
    summary="Identifica hasta 10 presentaciones en base a CN, nregistro o nombre",
    description=constant.identificar_medicamento,
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
        norm_query = _normalize(nombre)
        series_norm = filt["Presentación"].fillna("").apply(_normalize)
        filt['_norm'] = series_norm
        matches = filt[filt['_norm'].str.contains(norm_query)]
        if matches.empty:
            from difflib import get_close_matches
            similares = get_close_matches(norm_query, series_norm.tolist(), n=10, cutoff=0.7)
            matches = filt[filt['_norm'].isin(similares)]
        filt = matches.drop(columns=['_norm'])

    total = len(filt)
    page_df = _paginate(filt, pagina, page_size)
    docs = page_df.to_dict(orient="records")

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
    description="""
        Permite buscar y filtrar productos farmacéuticos por cualquiera de las columnas:
        Código Nacional, Nombre, Tipo de fármaco, Principio activo, Laboratorio, Estado,
        fechas de alta/baja, aportación, precios, agrupación, flags clínicos, etc.
    """,
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
