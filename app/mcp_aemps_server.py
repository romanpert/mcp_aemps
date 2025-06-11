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
import unicodedata
import asyncio
from datetime import datetime, timezone
from pathlib import Path
import logging
from typing import Any, List, Optional, Literal, AsyncIterator, Tuple, Dict

from fastapi import Body, FastAPI, HTTPException, Query, Depends, Request, WebSocket, Path as FPath
from fastapi_mcp import FastApiMCP
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials, OAuth2PasswordBearer
from fastapi.responses import JSONResponse, StreamingResponse

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
from app.helpers import _build_metadata, API_CIMA_AEMPS_VERSION

# VERSION API CIMA
API_PSUM_VERSION = "2.0"

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
    swagger_ui_parameters={"defaultModelsExpandDepth": -1},
)


# ---------------------------------------------------------------------------
#   Montar archivos estáticos
# ---------------------------------------------------------------------------
app.mount(
    "/data",
    StaticFiles(directory=str(settings.data_dir)),
    name="data"
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

        # Estructurar respuesta
        if isinstance(resultado, dict):
            respuesta = {**resultado, **metadatos}
        else:
            respuesta = {"data": resultado, **metadatos}

        logger.info(f"Medicamento encontrado exitosamente - CN: {cn_clean}")
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

    if isinstance(resultados, dict):
        respuesta = {**resultados, **metadatos}
    else:
        respuesta = {"data": resultados, **metadatos}

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

    if isinstance(resultados, dict):
        respuesta = {**resultados, **metadatos}
    else:
        respuesta = {"data": resultados, **metadatos}

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

    if isinstance(resultados, dict):
        # Fusionamos el contenido original con los metadatos
        return {**resultados, **metadatos}
    else:
        return {"data": resultados, **metadatos}


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

    try:
        # Si solo hay un CN
        if len(cn) == 1:
            detalle = await cima.presentacion(cn[0])
            parametros = {"cn": cn[0]}
            metadatos = _build_metadata(parametros)

            if isinstance(detalle, dict):
                return {**detalle, **metadatos}
            else:
                return {"data": detalle, **metadatos}

        # Múltiples CN: llamadas en paralelo
        tasks = [cima.presentacion(c) for c in cn]
        respuestas = await asyncio.gather(*tasks)

        result_dict: Dict[str, Any] = {}
        for codigo, detalle in zip(cn, respuestas):
            parametros = {"cn": codigo}
            metadatos = _build_metadata(parametros)

            if isinstance(detalle, dict):
                result_dict[codigo] = {**detalle, **metadatos}
            else:
                result_dict[codigo] = {"data": detalle, **metadatos}

        return result_dict

    except Exception:
        raise HTTPException(status_code=502, detail="Error upstream obteniendo presentación")


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
    practiv1: Optional[str] = Query(
        None,
        description="Nombre del principio activo principal."
    ),
    idpractiv1: Optional[str] = Query(
        None,
        description="ID del principio activo principal."
    ),
    dosis: Optional[str] = Query(
        None,
        description="Dosis del medicamento."
    ),
    forma: Optional[str] = Query(
        None,
        description="Nombre de la forma farmacéutica."
    ),
    atc: Optional[str] = Query(
        None,
        description="Código ATC o descripción parcial."
    ),
    nombre: Optional[str] = Query(
        None,
        description="Nombre del medicamento."
    ),
    modoArbol: Optional[int] = Query(
        None,
        description="Si se indica, devuelve resultados en modo jerárquico."
    ),
    pagina: Optional[int] = Query(
        1,
        ge=1,
        description="Número de página (si la API lo soporta)."
    ),
) -> Dict[str, Any]:
    resultados = await cima.vmpp(**locals())

    parametros = {
        "practiv1": practiv1,
        "idpractiv1": idpractiv1,
        "dosis": dosis,
        "forma": forma,
        "atc": atc,
        "nombre": nombre,
        "modoArbol": modoArbol,
        "pagina": pagina,
    }
    metadatos = _build_metadata(parametros)

    if isinstance(resultados, dict):
        return {**resultados, **metadatos}
    else:
        return {"data": resultados, **metadatos}

# ---------------------------------------------------------------------------
# 6 · Maestras — Metadata adaptada
# ---------------------------------------------------------------------------
@app.get(
    "/maestras",
    operation_id="consultar_maestras",
    summary="Consultar catálogos maestros: ATC, Principios Activos, Formas, Laboratorios...",
    description=constant.maestras_description,
    response_model=Dict[str, Any],  # Mejor definir un modelo Pydantic específico si se desea
)
async def consultar_maestras(
    maestra: Optional[int] = Query(
        None,
        description="ID de la maestra a consultar (1,3,4,6,7,11,13,14,15,16)."
    ),
    nombre: Optional[str] = Query(
        None,
        description="Nombre del elemento a recuperar."
    ),
    id: Optional[str] = Query(
        None,
        description="ID del elemento a recuperar."
    ),
    codigo: Optional[str] = Query(
        None,
        description="Código del elemento a recuperar."
    ),
    estupefaciente: Optional[int] = Query(
        None,
        ge=0,
        le=1,
        description="1 = Sólo principios activos estupefacientes."
    ),
    psicotropo: Optional[int] = Query(
        None,
        ge=0,
        le=1,
        description="1 = Sólo principios activos psicótropos."
    ),
    estuopsico: Optional[int] = Query(
        None,
        ge=0,
        le=1,
        description="1 = Principios activos estupefacientes o psicótropos."
    ),
    enuso: Optional[int] = Query(
        None,
        ge=0,
        le=1,
        description="0 = PA asociados o no asociados a medicamentos."
    ),
    pagina: Optional[int] = Query(
        1,
        ge=1,
        description="Número de página (si la API lo soporta)."
    ),
) -> Dict[str, Any]:
    resultados = await cima.maestras(**locals())

    # Construir metadatos usando la función auxiliar
    parametros = {
        "maestra": maestra,
        "nombre": nombre,
        "id": id,
        "codigo": codigo,
        "estupefaciente": estupefaciente,
        "psicotropo": psicotropo,
        "estuopsico": estuopsico,
        "enuso": enuso,
        "pagina": pagina,
    }
    metadatos = _build_metadata(parametros)

    if isinstance(resultados, dict):
        return {**resultados, **metadatos}
    else:
        return {"data": resultados, **metadatos}


# ---------------------------------------------------------------------------
# 7 · Registro de cambios — Metadata adaptada
# ---------------------------------------------------------------------------
@app.get(
    "/registro-cambios",
    operation_id="registro_cambios",
    summary="Historial de altas, bajas y modificaciones de medicamentos",
    description=constant.registro_cambios_description,
    response_model=Dict[str, Any],  # Mejor definir un modelo Pydantic específico si se desea
)
async def registro_cambios(
    fecha: Optional[str] = Query(
        None,
        description="Fecha a partir de la cual consultar cambios, formato dd/mm/yyyy."
    ),
    nregistro: Optional[str] = Query(
        None,
        description="Número de registro AEMPS (repetir para múltiples)."
    ),
    metodo: str = Query(
        "GET",
        regex="^(GET|POST)$",
        description="Método HTTP para la llamada interna (GET o POST)."
    ),
) -> Dict[str, Any]:
    resultados = await cima.registro_cambios(fecha=fecha, nregistro=nregistro, metodo=metodo)

    # Construir metadatos usando la función auxiliar
    parametros = {
        "fecha": fecha,
        "nregistro": nregistro,
        "metodo": metodo,
    }
    metadatos = _build_metadata(parametros)

    if isinstance(resultados, dict):
        return {**resultados, **metadatos}
    else:
        return {"data": resultados, **metadatos}


# ---------------------------------------------------------------------------
# 8 · Problemas de suministro — Metadata adaptada
# ---------------------------------------------------------------------------
@app.get(
    "/problemas-suministro",
    operation_id="problemas_suministro",
    summary="Consultar problemas de suministro por uno o varios CN",
    description=constant.problemas_suministro_description,
    response_model=Dict[str, Any],  # Mejor definir un modelo Pydantic específico si se desea
)
async def problemas_suministro(
    cn: Optional[List[str]] = Query(
        None,
        description="Uno o más Códigos Nacionales de la presentación. Repetir parámetro: ?cn=123&cn=456"
    )
) -> Dict[str, Any]:
    try:
        # Definición del mapa de tipos de problemas
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

        # Construir metadatos comunes
        parametros = {"cn": cn}
        metadatos = _build_metadata(parametros)
        # Incluir el diccionario de tipos en la metadata
        metadatos["metadata"]["tipo_problema_suministros"] = tipo_problema_suministros

        # Sin parámetros: listado global paginado (v1)
        if cn is None:
            resultado = await cima.psuministro(None)
            if isinstance(resultado, dict):
                return {**resultado, **metadatos}
            else:
                return {"data": resultado, **metadatos}

        # Con uno o varios CN: llamadas en paralelo
        tasks = [cima.psuministro(c) for c in cn]
        respuestas: List[Any] = await asyncio.gather(*tasks)

        result_dict: Dict[str, Any] = {}
        for codigo, resp in zip(cn, respuestas):
            if isinstance(resp, dict):
                result_dict[codigo] = {**resp, **metadatos}
            else:
                result_dict[codigo] = {"data": resp, **metadatos}

        return result_dict

    except ClientResponseError:
        raise HTTPException(status_code=502, detail="Error upstream en problemas de suministro")
    except Exception:
        raise HTTPException(status_code=500, detail="Error interno procesando problemas de suministro")


# ---------------------------------------------------------------------------
# 9 · Documentos segmentados – Secciones — Metadata adaptada
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

    if isinstance(resultados, dict):
        return {**resultados, **metadatos}
    else:
        return {"data": resultados, **metadatos}


# ---------------------------------------------------------------------------
# 9 · Documentos segmentados – Contenido — Metadata adaptada
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

    if isinstance(resultados, dict):
        return {**resultados, **metadatos}
    else:
        return {"data": resultados, **metadatos}


# ---------------------------------------------------------------------------
# 10 · Notas de seguridad — Metadata adaptada
# ---------------------------------------------------------------------------
@app.get(
    "/notas",
    operation_id="listar_notas",
    summary="Listado de notas de seguridad para uno o varios registros",
    description=constant.listar_notas_description,
    response_model=Dict[str, Any],  # Mejor definir un modelo Pydantic específico si se desea
)
async def listar_notas(
    nregistro: List[str] = Query(
        ...,  # obligatorio al menos uno
        description="Número(s) de registro. Repetir parámetro: ?nregistro=AAA&nregistro=BBB"
    )
) -> Dict[str, Any]:
    if not nregistro:
        raise HTTPException(
            status_code=400,
            detail="Se requiere al menos un 'nregistro'."
        )
    try:
        # Si solo es uno
        if len(nregistro) == 1:
            resultado = await cima.notas(nregistro=nregistro[0])
            parametros = {"nregistro": nregistro[0]}
            metadatos = _build_metadata(parametros)

            if isinstance(resultado, list):
                # Fusionamos cada item con metadatos
                return [ {**item, **metadatos} if isinstance(item, dict) else item for item in resultado ]

            return {"data": resultado, **metadatos}

        # Varios: llamadas en paralelo
        tasks = [cima.notas(nregistro=nr) for nr in nregistro]
        respuestas: List[Any] = await asyncio.gather(*tasks)

    except ClientResponseError:
        raise HTTPException(status_code=502, detail="Error upstream listando notas")
    except Exception:
        raise HTTPException(status_code=500, detail="Error interno procesando notas")

    # Empaquetar en dict { nregistro: lista con metadatos }
    result_dict: Dict[str, Any] = {}
    for nr, resp in zip(nregistro, respuestas):
        parametros = {"nregistro": nr}
        metadatos = _build_metadata(parametros)
        if isinstance(resp, list):
            result_dict[nr] = [ {**item, **metadatos} if isinstance(item, dict) else item for item in resp ]
        else:
            result_dict[nr] = {"data": resp, **metadatos}

    return result_dict


# ---------------------------------------------------------------------------
# 10 · Detalle de notas de seguridad — Metadata adaptada
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

    if isinstance(resultado, list):
        return [ {**item, **metadatos} if isinstance(item, dict) else item for item in resultado ]

    return {"data": resultado, **metadatos}

# ---------------------------------------------------------------------------
# 11 · Materiales informativos — Metadata adaptada
# ---------------------------------------------------------------------------
@app.get(
    "/materiales",
    operation_id="listar_materiales",
    summary="Listado de materiales informativos para uno o varios registros",
    description=constant.listar_materiales_description,
    response_model=Dict[str, Any],  # Mejor definir un modelo Pydantic específico si se desea
)
async def listar_materiales(
    nregistro: List[str] = Query(
        ..., description="Número(s) de registro. Repetir parámetro: ?nregistro=AAA&nregistro=BBB"
    )
) -> Dict[str, Any]:
    if not nregistro:
        raise HTTPException(
            status_code=400,
            detail="Se requiere al menos un 'nregistro'."
        )
    try:
        # Si solo hay uno
        if len(nregistro) == 1:
            resultado = await cima.materiales(nregistro=nregistro[0])
            # Metadata
            parametros = {"nregistro": nregistro[0]}
            metadatos = _build_metadata(parametros)

            if isinstance(resultado, list):
                return [
                    {**item, **metadatos} if isinstance(item, dict) else item 
                    for item in resultado
                ]
            return {"data": resultado, **metadatos}

        # Para varios: llamadas en paralelo
        tasks = [cima.materiales(nregistro=nr) for nr in nregistro]
        respuestas: List[Any] = await asyncio.gather(*tasks)

    except ClientResponseError:
        raise HTTPException(status_code=502, detail="Error upstream listando materiales")
    except Exception:
        raise HTTPException(status_code=500, detail="Error interno procesando materiales")

    # Empaquetar resultados por registro con metadata
    result_dict: Dict[str, Any] = {}
    for nr, res in zip(nregistro, respuestas):
        parametros = {"nregistro": nr}
        metadatos = _build_metadata(parametros)

        if isinstance(res, list):
            result_dict[nr] = [
                {**item, **metadatos} if isinstance(item, dict) else item 
                for item in res
            ]
        else:
            result_dict[nr] = {"data": res, **metadatos}

    return result_dict


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

    if isinstance(resultado, list):
        return [
            {**item, **metadatos} if isinstance(item, dict) else item 
            for item in resultado
        ]
    return {"data": resultado, **metadatos}


# ---------------------------------------------------------------------------
# 12a · HTML completo de ficha técnica — Metadata adaptada
# ---------------------------------------------------------------------------
@app.get(
    "/doc-html/ft",
    operation_id="html_ficha_tecnica_multiple",
    summary="HTML completo de ficha técnica para uno o varios registros",
    description=constant.html_ft_multiple_description,
    response_model=Dict[str, Any],  # Ahora devuelve HTML por registro + metadata
)
async def html_ficha_tecnica_multiple(
    nregistro: List[str] = Query(
        ...,  # obligatorio al menos uno
        description="Número(s) de registro. Repetir parámetro: ?nregistro=AAA&nregistro=BBB"
    ),
    filename: str = Query(
        ...,  # obligatorio
        description="Nombre de archivo HTML ('FichaTecnica.html')"
    )
) -> Any:
    if not nregistro or not filename:
        raise HTTPException(
            status_code=400,
            detail="Se requiere al menos un 'nregistro' y un 'filename'."
        )

    try:
        # Si solo hay uno: devolvemos el streaming tal cual
        if len(nregistro) == 1:
            content = await cima.get_html(tipo="ft", nregistro=nregistro[0], filename=filename)
            return StreamingResponse(content, media_type="text/html")

        # Varios registros: paralelizar descargas
        tasks = [
            cima.get_html(tipo="ft", nregistro=nr, filename=filename)
            for nr in nregistro
        ]
        contenidos = await asyncio.gather(*tasks)
    except ClientResponseError:
        raise HTTPException(status_code=502, detail="Error upstream descargando ficha técnica")
    except Exception:
        raise HTTPException(status_code=500, detail="Error interno procesando ficha técnica")

    # Construir dict con el HTML de cada registro como texto
    html_dict = {
        nr: contenido.read().decode("utf-8")
        for nr, contenido in zip(nregistro, contenidos)
    }

    # Inyectar metadata uniforme
    parametros = {"nregistro": nregistro, "filename": filename}
    metadatos = _build_metadata(parametros)

    # Devolvemos el HTML por registro más la metadata al mismo nivel
    return {**html_dict, **metadatos}


@app.get(
    "/doc-html/ft/{nregistro}/{filename}",
    operation_id="html_ficha_tecnica",
    summary="HTML completo de ficha técnica (único registro)",
    description=constant.html_ft_description,
)
async def html_ficha_tecnica(
    nregistro: str = Path(..., description="Número de registro"),
    filename: str = Path(..., description="Nombre de archivo HTML ('FichaTecnica.html')")
) -> StreamingResponse:
    # Para la versión de un solo registro mantenemos el streaming puro
    content = await cima.get_html(tipo="ft", nregistro=nregistro, filename=filename)
    return StreamingResponse(content, media_type="text/html")


# ---------------------------------------------------------------------------
# 12b · HTML completo de prospecto — Metadata adaptada
# ---------------------------------------------------------------------------
@app.get(
    "/doc-html/p",
    operation_id="html_prospecto_multiple",
    summary="HTML completo de prospecto para uno o varios registros",
    description=constant.html_p_multiple_description,
    response_model=Dict[str, Any],  # Ahora devuelve HTML por registro + metadata
)
async def html_prospecto_multiple(
    nregistro: List[str] = Query(
        ..., description="Número(s) de registro. Repetir parámetro: ?nregistro=AAA&nregistro=BBB"
    ),
    filename: str = Query(
        ..., description="Nombre de archivo HTML ('Prospecto.html' o sección específica)"
    )
) -> Any:
    if not nregistro or not filename:
        raise HTTPException(
            status_code=400,
            detail="Se requiere al menos un 'nregistro' y un 'filename'."
        )
    try:
        # Un registro: streaming sin metadata
        if len(nregistro) == 1:
            content = await cima.get_html(tipo="p", nregistro=nregistro[0], filename=filename)
            return StreamingResponse(content, media_type="text/html")

        # Varios registros: paralelizar descargas
        tasks = [
            cima.get_html(tipo="p", nregistro=nr, filename=filename)
            for nr in nregistro
        ]
        contenidos = await asyncio.gather(*tasks)
    except ClientResponseError:
        raise HTTPException(status_code=502, detail="Error upstream descargando prospecto")
    except Exception:
        raise HTTPException(status_code=500, detail="Error interno procesando prospecto")

    # Construir dict con el HTML de cada registro como texto
    html_dict = {
        nr: contenido.read().decode("utf-8")
        for nr, contenido in zip(nregistro, contenidos)
    }

    # Inyectar metadata uniforme
    parametros = {"nregistro": nregistro, "filename": filename}
    metadatos = _build_metadata(parametros)

    # Devolvemos el HTML por registro más la metadata al mismo nivel
    return {**html_dict, **metadatos}


@app.get(
    "/doc-html/p/{nregistro}/{filename}",
    operation_id="html_prospecto",
    summary="HTML completo de prospecto (único registro)",
    description=constant.html_p_description,
)
async def html_prospecto(
    nregistro: str = Path(..., description="Número de registro"),
    filename: str = Path(..., description="Nombre de archivo HTML ('Prospecto.html' o sección específica)")
) -> StreamingResponse:
    # Para la versión de un solo registro mantenemos el streaming puro
    content = await cima.get_html(tipo="p", nregistro=nregistro, filename=filename)
    return StreamingResponse(content, media_type="text/html")



# AUX FUNCTION
def _normalize(s: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", s.lower())
        if unicodedata.category(c) != "Mn"
    )

# ---------------------------------------------------------------------------
# 12c · Descargar Informe de Posicionamiento Terapéutico (IPT) — Metadata adaptada
# ---------------------------------------------------------------------------
@app.get(
    "/descargar-ipt",
    operation_id="descargar_ipt",
    summary="Descargar Informe de Posicionamiento Terapéutico (IPT) para uno o varios CN o registros",
    description=constant.descargar_ipt,
    response_model=Dict[str, Any],
)
async def descargar_ipt(
    cn: Optional[List[str]] = Query(
        None,
        description="Código(s) Nacional(es) del medicamento. Repetir parámetro: ?cn=AAA&cn=BBB"
    ),
    nregistro: Optional[List[str]] = Query(
        None,
        description="Número(s) de registro AEMPS. Repetir parámetro: ?nregistro=AAA&nregistro=BBB"
    )
) -> Dict[str, Any]:
    if not cn and not nregistro:
        raise HTTPException(
            status_code=400,
            detail="Debe especificar al menos un 'cn' o un 'nregistro'."
        )

    tasks: List[Any] = []
    if cn:
        for c in cn:
            tasks.append(cima.download_docs(cn=c, nregistro=None, tipos=["ipt"]))
    if nregistro:
        for nr in nregistro:
            tasks.append(cima.download_docs(cn=None, nregistro=nr, tipos=["ipt"]))

    try:
        resultados_list: List[List[str]] = await asyncio.gather(*tasks)
    except ClientResponseError:
        raise HTTPException(status_code=502, detail="Error upstream descargando IPT")
    except Exception:
        raise HTTPException(status_code=500, detail="Error interno procesando IPT")

    # Aplanar listas de rutas
    all_paths: List[str] = [path for sub in resultados_list for path in sub]

    # Construir metadata usando la función auxiliar
    parametros = {"cn": cn, "nregistro": nregistro}
    metadatos = _build_metadata(parametros)

    # Devolver rutas + metadata
    return {"data": all_paths, **metadatos}


# ---------------------------------------------------------------------------
# 13 · Identificar medicamento en Presentaciones.xls — Metadata adaptada
# ---------------------------------------------------------------------------
@app.get(
    "/identificar-medicamento",
    operation_id="identificar_medicamento",
    summary="Identifica hasta 10 presentaciones en base a CN, nregistro o nombre",
    description=constant.identificar_medicamento,
    response_model=Dict[str, Any],  # Ahora incluimos data + metadata
)
async def identificar_medicamento(
    nregistro:    Optional[str] = Query(None),
    cn:           Optional[str] = Query(None),
    nombre:       Optional[str] = Query(None),
    laboratorio:  Optional[str] = Query(None),
    atc:          Optional[str] = Query(None),
    estado:       Optional[str] = Query(None),
    comercializado: Optional[bool] = Query(None),
    pagina:       int = Query(1, ge=1),
) -> Dict[str, Any]:
    df = app.state.df_presentaciones

    # Empiezo con todo el df...
    filt = df

    if nregistro:
        filt = filt[filt["Nº Registro"].astype(str)==nregistro]
    if cn:
        filt = filt[filt["Cod. Nacional"].astype(str)==cn]
    if laboratorio:
        filt = filt[filt["Laboratorio"]].str.contains(laboratorio, case=False, na=False)
    if atc:
        filt = filt[filt["Cód. ATC"].str.contains(atc, case=False, na=False)]
    if estado:
        filt = filt[filt["Estado"].str.contains(estado, case=False, na=False)]
    if comercializado is not None:
        # asumimos columna “¿Comercializado?” con “SI”/“NO”
        val = "SI" if comercializado else "NO"
        filt = filt[filt["¿Comercializado?"]==val]

    # filtro por nombre con normalización/fuzzy como antes
    if nombre:
        norm = _normalize(nombre)
        opciones = filt["Presentación"].fillna("").apply(_normalize)
        df_aux = filt.assign(_norm=opciones)
        matches = df_aux[df_aux["_norm"].str.contains(norm)]
        if matches.empty:
            from difflib import get_close_matches
            similares = get_close_matches(norm, opciones.tolist(), n=10, cutoff=0.7)
            matches = df_aux[df_aux["_norm"].isin(similares)]
        filt = matches.drop(columns="_norm")

    # paginación sencilla
    start = (pagina-1)*10
    end = start+10
    docs = filt.iloc[start:end].to_dict(orient="records")

    # metadata
    metadatos = _build_metadata({
        "nregistro": nregistro,
        "cn": cn,
        "nombre": nombre,
        "laboratorio": laboratorio,
        "atc": atc,
        "estado": estado,
        "comercializado": comercializado,
        "pagina": pagina,
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
        Todas las validaciones de tipo y formato aparecen en la especificación OpenAPI.
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

    df: pd.DataFrame = app.state.df_nomenclator
    filt = df.copy()

    # Filtros exactos / parciales
    if codigo_nacional:
        filt = filt[filt["Código Nacional"].astype(str) == codigo_nacional]
    if nombre_producto:
        filt = filt[filt["Nombre del producto farmacéutico"]
                    .str.contains(nombre_producto, case=False, na=False)]
    if tipo_farmaco:
        filt = filt[filt["Tipo de fármaco"]
                    .str.contains(tipo_farmaco, case=False, na=False)]
    if principio_activo:
        filt = filt[filt["Principio activo o asociación de principios activos"]
                    .str.contains(principio_activo, case=False, na=False)]
    if codigo_laboratorio:
        filt = filt[filt["Código del laboratorio ofertante"].astype(str) == codigo_laboratorio]
    if nombre_laboratorio:
        filt = filt[filt["Nombre del laboratorio ofertante"]
                    .str.contains(nombre_laboratorio, case=False, na=False)]
    if estado:
        filt = filt[filt["Estado"].str.contains(estado, case=False, na=False)]
    if aportacion_beneficiario:
        filt = filt[filt["Aportación del beneficiario"]
                    .str.contains(aportacion_beneficiario, case=False, na=False)]
    if agrupacion_codigo:
        filt = filt[filt["Código de la agrupación homogénea del producto sanitario"]
                    .astype(str) == agrupacion_codigo]
    if agrupacion_nombre:
        filt = filt[filt["Nombre de la agrupación homogénea del producto sanitario"]
                    .str.contains(agrupacion_nombre, case=False, na=False)]

    # Filtros numéricos
    if precio_min_iva is not None:
        filt = filt[filt["Precio venta al público con IVA"].astype(float) >= precio_min_iva]
    if precio_max_iva is not None:
        filt = filt[filt["Precio venta al público con IVA"].astype(float) <= precio_max_iva]

    # Filtros booleanos
    bool_map = {True: "SI", False: "NO"}
    if diagnostico_hospitalario is not None:
        filt = filt[filt["Diagnóstico hospitalario"].map(bool_map) == bool_map[diagnostico_hospitalario]]
    if larga_duracion is not None:
        filt = filt[filt["Tratamiento de larga duración"].map(bool_map) == bool_map[larga_duracion]]
    if especial_control is not None:
        filt = filt[filt["Especial control médico"].map(bool_map) == bool_map[especial_control]]
    if medicamento_huerfano is not None:
        filt = filt[filt["Medicamento huérfano"].map(bool_map) == bool_map[medicamento_huerfano]]

    # Filtros de fecha
    def _parse(d: str) -> datetime:
        return datetime.strptime(d, "%d/%m/%Y")
    if fecha_alta_desde:
        d0 = _parse(fecha_alta_desde)
        filt = filt[pd.to_datetime(filt["Fecha de alta en el nomenclátor"], dayfirst=True) >= d0]
    if fecha_alta_hasta:
        d1 = _parse(fecha_alta_hasta)
        filt = filt[pd.to_datetime(filt["Fecha de alta en el nomenclátor"], dayfirst=True) <= d1]
    if fecha_baja_desde:
        d2 = _parse(fecha_baja_desde)
        filt = filt[pd.to_datetime(filt["Fecha de baja en el nomenclátor"], dayfirst=True) >= d2]
    if fecha_baja_hasta:
        d3 = _parse(fecha_baja_hasta)
        filt = filt[pd.to_datetime(filt["Fecha de baja en el nomenclátor"], dayfirst=True) <= d3]

    # Paginación dinámica
    start = (pagina - 1) * page_size
    end = start + page_size
    records = filt.iloc[start:end].to_dict(orient="records")

    # Metadata incluyendo page_size
    metadatos = _build_metadata({
        "codigo_nacional":          codigo_nacional,
        "nombre_producto":          nombre_producto,
        "tipo_farmaco":             tipo_farmaco,
        "principio_activo":         principio_activo,
        "codigo_laboratorio":       codigo_laboratorio,
        "nombre_laboratorio":       nombre_laboratorio,
        "estado":                   estado,
        "fecha_alta_desde":         fecha_alta_desde,
        "fecha_alta_hasta":         fecha_alta_hasta,
        "fecha_baja_desde":         fecha_baja_desde,
        "fecha_baja_hasta":         fecha_baja_hasta,
        "aportacion_beneficiario":  aportacion_beneficiario,
        "precio_min_iva":           precio_min_iva,
        "precio_max_iva":           precio_max_iva,
        "agrupacion_codigo":        agrupacion_codigo,
        "agrupacion_nombre":        agrupacion_nombre,
        "diagnostico_hospitalario": diagnostico_hospitalario,
        "larga_duracion":           larga_duracion,
        "especial_control":         especial_control,
        "medicamento_huerfano":     medicamento_huerfano,
        "pagina":                   pagina,
        "page_size":                page_size,
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

# Mount MCP
mcp.mount()

mcp.setup_server()
