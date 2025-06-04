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
# 4 · Presentaciones (listado + detalle)
# ---------------------------------------------------------------------------
@app.get(
    "/presentaciones",
    operation_id="listar_presentaciones",
    summary="Listar presentaciones de un medicamento con filtros (cn, nregistro, etc.)",
    description=constant.presentaciones_description,
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
) -> Any:
    resultados = await cima.presentaciones(**locals())

    # Añadir metadatos obligatorios: atribución, fecha y descargo de responsabilidad
    fecha_hoy = datetime.utcnow().strftime("%d/%m/%Y")
    meta = {
        "datos_obtenidos": f"Datos CIMA (AEMPS) extraídos el {fecha_hoy}.",
        "descargo": "Esta información no constituye consejo médico; se proporciona solo a efectos informativos."
    }

    if isinstance(resultados, dict):
        resultados.setdefault("meta", {}).update(meta)

    return resultados


@app.get(
    "/presentacion",
    operation_id="obtener_presentacion",
    summary="Detalle de una o varias presentaciones (por uno o varios CN)",
    description=constant.presentacion_description,
)
async def obtener_presentacion(
    cn: List[str] = Query(
        ...,  # obligatorio al menos un CN
        description="Uno o varios Códigos Nacionales. Repetir el parámetro: ?cn=765432&cn=654321"
    )
) -> Any:
    if not cn:
        raise HTTPException(
            status_code=400,
            detail="Debe indicar al menos un 'cn'."
        )
    try:
        # Si solo hay un CN, devolvemos directamente el detalle
        if len(cn) == 1:
            detalle = await cima.presentacion(cn[0])
            # Añadir metadatos
            fecha_hoy = datetime.utcnow().strftime("%d/%m/%Y")
            if isinstance(detalle, dict):
                detalle.setdefault("meta", {}).update({
                    "datos_obtenidos": f"Datos CIMA (AEMPS) extraídos el {fecha_hoy}.",
                    "descargo": "Esta información no constituye consejo médico; se proporciona solo a efectos informativos."
                })
            return detalle

        # Múltiples CN: disparamos llamadas en paralelo
        tasks = [cima.presentacion(c) for c in cn]
        respuestas = await asyncio.gather(*tasks)

        # Construimos un dict { cn: detalle }
        result_dict: dict[str, Any] = {}
        fecha_hoy = datetime.utcnow().strftime("%d/%m/%Y")
        for codigo, detalle in zip(cn, respuestas):
            if isinstance(detalle, dict):
                detalle.setdefault("meta", {}).update({
                    "datos_obtenidos": f"Datos CIMA (AEMPS) extraídos el {fecha_hoy}.",
                    "descargo": "Esta información no constituye consejo médico; se proporciona solo a efectos informativos."
                })
            result_dict[codigo] = detalle

        return result_dict

    except Exception as e:
        # ClientResponseError se liberará como 502 si es upstream distinto de 404
        raise HTTPException(status_code=502, detail="Error upstream obteniendo presentación")

# ---------------------------------------------------------------------------
# 5 · VMP/VMPP
# ---------------------------------------------------------------------------
@app.get(
    "/vmpp",
    operation_id="buscar_vmpp",
    summary="Equivalentes clínicos VMP/VMPP filtrables por principio activo, dosis, forma, etc.",
    description=constant.vmpp_description,
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
) -> Any:
    resultados = await cima.vmpp(**locals())

    # Añadir metadatos obligatorios: atribución, fecha y descargo de responsabilidad
    fecha_hoy = datetime.utcnow().strftime("%d/%m/%Y")
    meta = {
        "datos_obtenidos": f"Datos CIMA (AEMPS) extraídos el {fecha_hoy}.",
        "descargo": "Esta información no constituye consejo médico; se proporciona solo a efectos informativos."
    }

    if isinstance(resultados, dict):
        resultados.setdefault("meta", {}).update(meta)

    return resultados


# ---------------------------------------------------------------------------
# 6 · Maestras
# ---------------------------------------------------------------------------
@app.get(
    "/maestras",
    operation_id="consultar_maestras",
    summary="Consultar catálogos maestros: ATC, Principios Activos, Formas, Laboratorios...",
    description=constant.maestras_description,
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
) -> Any:
    resultados = await cima.maestras(**locals())

    # Añadir metadatos obligatorios: atribución, fecha y descargo de responsabilidad
    fecha_hoy = datetime.utcnow().strftime("%d/%m/%Y")
    meta = {
        "datos_obtenidos": f"Datos CIMA (AEMPS) extraídos el {fecha_hoy}.",
        "descargo": "Esta información no constituye consejo médico; se proporciona solo a efectos informativos."
    }

    if isinstance(resultados, dict):
        resultados.setdefault("meta", {}).update(meta)

    return resultados

# ---------------------------------------------------------------------------
# 7 · Registro de cambios
# ---------------------------------------------------------------------------
@app.get(
    "/registro-cambios",
    operation_id="registro_cambios",
    summary="Historial de altas, bajas y modificaciones de medicamentos",
    description=constant.registro_cambios_description,
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
) -> Any:
    resultados = await cima.registro_cambios(fecha=fecha, nregistro=nregistro, metodo=metodo)

    # Añadir metadatos obligatorios: atribución, fecha y descargo de responsabilidad
    fecha_hoy = datetime.utcnow().strftime("%d/%m/%Y")
    meta = {
        "datos_obtenidos": f"Datos CIMA (AEMPS) extraídos el {fecha_hoy}.",
        "descargo": "Esta información no constituye consejo médico; se proporciona solo a efectos informativos."
    }

    if isinstance(resultados, dict):
        resultados.setdefault("meta", {}).update(meta)

    return resultados


# ---------------------------------------------------------------------------
# 8 · Problemas de suministro (endpoint FastAPI-MCP)
# ---------------------------------------------------------------------------
@app.get(
    "/problemas-suministro",
    operation_id="problemas_suministro",
    summary="Consultar problemas de suministro por uno o varios CN",
    description=constant.problemas_suministro_description
)
async def problemas_suministro(
    cn: Optional[List[str]] = Query(
        None,
        description="Uno o más Códigos Nacionales de la presentación. Repetir parámetro: ?cn=123&cn=456"
    )
) -> Any:
    try:
        # Definir diccionario tipo_problema_suministros
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

        # Sin parámetros: listado global paginado (v1)
        if cn is None:
            resultado = await cima.psuministro(None)
            # Agregar meta con tipo_problema_suministros
            fecha_hoy = datetime.utcnow().strftime("%d/%m/%Y")
            if isinstance(resultado, dict):
                resultado.setdefault("meta", {}).update({
                    "datos_obtenidos": f"Datos CIMA (AEMPS) extraídos el {fecha_hoy}.",
                    "descargo": "Esta información no constituye consejo médico; se proporciona solo a efectos informativos.",
                    "tipo_problema_suministros": tipo_problema_suministros
                })
            return resultado

        # Con uno o varios CN: llamadas en paralelo
        tasks = [cima.psuministro(c) for c in cn]
        respuestas: List[Any] = await asyncio.gather(*tasks)

        fecha_hoy = datetime.utcnow().strftime("%d/%m/%Y")
        result_dict = {}
        for codigo, resp in zip(cn, respuestas):
            if isinstance(resp, dict):
                resp.setdefault("meta", {}).update({
                    "datos_obtenidos": f"Datos CIMA (AEMPS) extraídos el {fecha_hoy}.",
                    "descargo": "Esta información no constituye consejo médico; se proporciona solo a efectos informativos.",
                    "tipo_problema_suministros": tipo_problema_suministros
                })
            result_dict[codigo] = resp

        return result_dict

    except ClientResponseError:
        raise HTTPException(status_code=502, detail="Error upstream en problemas de suministro")
    except Exception:
        raise HTTPException(status_code=500, detail="Error interno procesando problemas de suministro")


# ---------------------------------------------------------------------------
# 9 · Documentos segmentados – Secciones
# ---------------------------------------------------------------------------
@app.get(
    "/doc-secciones/{tipo_doc}",
    operation_id="doc_secciones",
    summary="Metadatos de secciones de Ficha Técnica/prospecto",
    description=constant.doc_secciones_description,
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
) -> Any:
    if not (nregistro or cn):
        raise HTTPException(status_code=400, detail="Se requiere 'nregistro' o 'cn'.")

    resultados = await cima.doc_secciones(tipo_doc, nregistro=nregistro, cn=cn)

    fecha_hoy = datetime.utcnow().strftime("%d/%m/%Y")
    meta = {
        "datos_obtenidos": f"Datos CIMA (AEMPS) extraídos el {fecha_hoy}.",
        "descargo": "Esta información no constituye consejo médico; se proporciona solo a efectos informativos."
    }

    if isinstance(resultados, dict):
        resultados.setdefault("meta", {}).update(meta)

    return resultados


# ---------------------------------------------------------------------------
# 9 · Documentos segmentados – Contenido
# ---------------------------------------------------------------------------
@app.get(
    "/doc-contenido/{tipo_doc}",
    operation_id="doc_contenido",
    summary="Contenido HTML/JSON de secciones de Ficha Técnica/prospecto",
    description=constant.doc_contenido_description,
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
) -> Any:
    if not (nregistro or cn):
        raise HTTPException(status_code=400, detail="Se requiere 'nregistro' o 'cn'.")

    resultados = await cima.doc_contenido(tipo_doc, nregistro=nregistro, cn=cn, seccion=seccion)

    fecha_hoy = datetime.utcnow().strftime("%d/%m/%Y")
    meta = {
        "datos_obtenidos": f"Datos CIMA (AEMPS) extraídos el {fecha_hoy}.",
        "descargo": "Esta información no constituye consejo médico; se proporciona solo a efectos informativos."
    }

    if isinstance(resultados, dict):
        resultados.setdefault("meta", {}).update(meta)

    return resultados


# ---------------------------------------------------------------------------
# 10 · Notas de seguridad (soportando varios números de registro)
# ---------------------------------------------------------------------------
@app.get(
    "/notas",
    operation_id="listar_notas",
    summary="Listado de notas de seguridad para uno o varios registros",
    description=constant.listar_notas_description,
)
async def listar_notas(
    nregistro: List[str] = Query(
        ...,  # obligatorio al menos uno
        description="Número(s) de registro. Repetir parámetro: ?nregistro=AAA&nregistro=BBB"
    )
) -> Any:
    if not nregistro:
        raise HTTPException(
            status_code=400,
            detail="Se requiere al menos un 'nregistro'."
        )
    try:
        # Si solo es uno, devolvemos la lista habitual
        if len(nregistro) == 1:
            resultado = await cima.notas(nregistro=nregistro[0])
            fecha_hoy = datetime.utcnow().strftime("%d/%m/%Y")
            if isinstance(resultado, list):
                for item in resultado:
                    if isinstance(item, dict):
                        item.setdefault("meta", {}).update({
                            "datos_obtenidos": f"Datos CIMA (AEMPS) extraídos el {fecha_hoy}.",
                            "descargo": "Esta información no constituye consejo médico; se proporciona solo a efectos informativos."
                        })
            return resultado

        # Para varios: lanzamos todas las llamadas en paralelo
        tasks = [cima.notas(nregistro=nr) for nr in nregistro]
        respuestas: List[Any] = await asyncio.gather(*tasks)
    except ClientResponseError:
        raise HTTPException(status_code=502, detail="Error upstream listando notas")
    except Exception:
        raise HTTPException(status_code=500, detail="Error interno procesando notas")

    # Empaquetar en { nregistro: resultado }
    fecha_hoy = datetime.utcnow().strftime("%d/%m/%Y")
    result_dict = {}
    for nr, resp in zip(nregistro, respuestas):
        if isinstance(resp, list):
            for item in resp:
                if isinstance(item, dict):
                    item.setdefault("meta", {}).update({
                        "datos_obtenidos": f"Datos CIMA (AEMPS) extraídos el {fecha_hoy}.",
                        "descargo": "Esta información no constituye consejo médico; se proporciona solo a efectos informativos."
                    })
        result_dict[nr] = resp

    return result_dict


@app.get(
    "/notas/{nregistro}",
    operation_id="obtener_notas",
    summary="Detalle de notas de seguridad de un registro",
    description=constant.obtener_notas_description,
)
async def obtener_notas(
    nregistro: str = Path(
        ..., description="Número de registro"
    )
) -> Any:
    resultado = await cima.notas(nregistro=nregistro)
    fecha_hoy = datetime.utcnow().strftime("%d/%m/%Y")
    if isinstance(resultado, list):
        for item in resultado:
            if isinstance(item, dict):
                item.setdefault("meta", {}).update({
                    "datos_obtenidos": f"Datos CIMA (AEMPS) extraídos el {fecha_hoy}.",
                    "descargo": "Esta información no constituye consejo médico; se proporciona solo a efectos informativos."
                })
    return resultado

# ---------------------------------------------------------------------------
# 11 · Materiales informativos (soportando varios números de registro)
# ---------------------------------------------------------------------------
@app.get(
    "/materiales",
    operation_id="listar_materiales",
    summary="Listado de materiales informativos para uno o varios registros",
    description=constant.listar_materiales_description,
)
async def listar_materiales(
    nregistro: List[str] = Query(
        ..., description="Número(s) de registro. Repetir parámetro: ?nregistro=AAA&nregistro=BBB"
    )
) -> Any:
    if not nregistro:
        raise HTTPException(
            status_code=400,
            detail="Se requiere al menos un 'nregistro'."
        )
    try:
        # Si solo hay uno, mantenemos comportamiento original
        if len(nregistro) == 1:
            resultado = await cima.materiales(nregistro=nregistro[0])
            fecha_hoy = datetime.utcnow().strftime("%d/%m/%Y")
            if isinstance(resultado, list):
                for item in resultado:
                    if isinstance(item, dict):
                        item.setdefault("meta", {}).update({
                            "datos_obtenidos": f"Datos CIMA (AEMPS) extraídos el {fecha_hoy}.",
                            "descargo": "Esta información no constituye consejo médico; se proporciona solo a efectos informativos."
                        })
            return resultado

        # Para varios: lanzamos todas las llamd asistir
        tasks = [cima.materiales(nregistro=nr) for nr in nregistro]
        resultados = await asyncio.gather(*tasks)
    except ClientResponseError:
        raise HTTPException(status_code=502, detail="Error upstream listando materiales")
    except Exception:
        raise HTTPException(status_code=500, detail="Error interno procesando materiales")

    fecha_hoy = datetime.utcnow().strftime("%d/%m/%Y")
    result_dict = {}
    for nr, res in zip(nregistro, resultados):
        if isinstance(res, list):
            for item in res:
                if isinstance(item, dict):
                    item.setdefault("meta", {}).update({
                        "datos_obtenidos": f"Datos CIMA (AEMPS) extraídos el {fecha_hoy}.",
                        "descargo": "Esta información no constituye consejo médico; se proporciona solo a efectos informativos."
                    })
        result_dict[nr] = res

    return result_dict


@app.get(
    "/materiales/{nregistro}",
    operation_id="obtener_materiales",
    summary="Detalle de materiales informativos de un registro",
    description=constant.obtener_materiales_description,
)
async def obtener_materiales(
    nregistro: str = Path(
        ..., description="Número de registro"
    )
) -> Any:
    resultado = await cima.materiales(nregistro=nregistro)
    fecha_hoy = datetime.utcnow().strftime("%d/%m/%Y")
    if isinstance(resultado, list):
        for item in resultado:
            if isinstance(item, dict):
                item.setdefault("meta", {}).update({
                    "datos_obtenidos": f"Datos CIMA (AEMPS) extraídos el {fecha_hoy}.",
                    "descargo": "Esta información no constituye consejo médico; se proporciona solo a efectos informativos."
                })
    return resultado

# ---------------------------------------------------------------------------
# 12a · HTML completo de ficha técnica (soportando varios registros)
# ---------------------------------------------------------------------------
@app.get(
    "/doc-html/ft",
    operation_id="html_ficha_tecnica_multiple",
    summary="HTML completo de ficha técnica para uno o varios registros",
    description=constant.html_ft_multiple_description,
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
        # Un solo registro: streaming como antes
        if len(nregistro) == 1:
            content = await cima.get_html(tipo="ft", nregistro=nregistro[0], filename=filename)
            return StreamingResponse(content, media_type="text/html")

        # Varios registros: paralelizar descargas
        tasks = [
            cima.get_html(tipo="ft", nregistro=nr, filename=filename) for nr in nregistro
        ]
        contenidos = await asyncio.gather(*tasks)
    except ClientResponseError:
        raise HTTPException(status_code=502, detail="Error upstream descargando ficha técnica")
    except Exception:
        raise HTTPException(status_code=500, detail="Error interno procesando ficha técnica")

    # Construir dict con HTML de cada registro como texto
    html_dict = {
        nr: contenido.read().decode("utf-8") for nr, contenido in zip(nregistro, contenidos)
    }
    # Añadir metadatos obligatorios
    fecha_hoy = datetime.utcnow().strftime("%d/%m/%Y")
    meta = {
        "datos_obtenidos": f"Datos CIMA (AEMPS) extraídos el {fecha_hoy}.",
        "descargo": "Esta información no constituye consejo médico; se proporciona solo a efectos informativos."
    }
    html_dict["meta"] = meta

    return html_dict


@app.get(
    "/doc-html/ft/{nregistro}/{filename}",
    operation_id="html_ficha_tecnica",
    summary="HTML completo de ficha técnica (único registro)",
    description=constant.html_ft_description,
)
async def html_ficha_tecnica(
    nregistro: str = Path(..., description="Número de registro"),
    filename: str = Path(
        ..., description="Nombre de archivo HTML ('FichaTecnica.html')"
    )
) -> StreamingResponse:
    content = await cima.get_html(tipo="ft", nregistro=nregistro, filename=filename)
    return StreamingResponse(content, media_type="text/html")

# ---------------------------------------------------------------------------
# 12b · HTML completo de prospecto (soportando varios registros)
# ---------------------------------------------------------------------------
@app.get(
    "/doc-html/p",
    operation_id="html_prospecto_multiple",
    summary="HTML completo de prospecto para uno o varios registros",
    description=constant.html_p_multiple_description,
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
        if len(nregistro) == 1:
            content = await cima.get_html(tipo="p", nregistro=nregistro[0], filename=filename)
            return StreamingResponse(content, media_type="text/html")

        tasks = [cima.get_html(tipo="p", nregistro=nr, filename=filename) for nr in nregistro]
        contenidos = await asyncio.gather(*tasks)
    except ClientResponseError:
        raise HTTPException(status_code=502, detail="Error upstream descargando prospecto")
    except Exception:
        raise HTTPException(status_code=500, detail="Error interno procesando prospecto")

    html_dict = {
        nr: contenido.read().decode("utf-8") for nr, contenido in zip(nregistro, contenidos)
    }
    # Añadir metadatos obligatorios
    fecha_hoy = datetime.utcnow().strftime("%d/%m/%Y")
    meta = {
        "datos_obtenidos": f"Datos CIMA (AEMPS) extraídos el {fecha_hoy}.",
        "descargo": "Esta información no constituye consejo médico; se proporciona solo a efectos informativos."
    }
    html_dict["meta"] = meta

    return html_dict


@app.get(
    "/doc-html/p/{nregistro}/{filename}",
    operation_id="html_prospecto",
    summary="HTML completo de prospecto (único registro)",
    description=constant.html_p_description,
)
async def html_prospecto(
    nregistro: str = Path(..., description="Número de registro"),
    filename: str = Path(
        ..., description="Nombre de archivo HTML ('Prospecto.html' o sección específica)"
    )
) -> StreamingResponse:
    content = await cima.get_html(tipo="p", nregistro=nregistro, filename=filename)
    return StreamingResponse(content, media_type="text/html")


# AUX FUNCTION
def _normalize(s: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", s.lower())
        if unicodedata.category(c) != "Mn"
    )

# ---------------------------------------------------------------------------
# 12c · Descargar Informe de Posicionamiento Terapéutico (IPT) para varios registros o CN
# ---------------------------------------------------------------------------
@app.get(
    "/descargar-ipt",
    operation_id="descargar_ipt",
    summary="Descargar Informe de Posicionamiento Terapéutico (IPT) para uno o varios CN o registros",
    description=constant.descargar_ipt,
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
) -> Any:
    if not cn and not nregistro:
        raise HTTPException(
            status_code=400,
            detail="Debe especificar al menos un 'cn' o un 'nregistro'."
        )

    tasks = []
    if cn:
        for c in cn:
            tasks.append(cima.download_docs(cn=c, nregistro=None, tipos=["ipt"]))
    if nregistro:
        for nr in nregistro:
            tasks.append(cima.download_docs(cn=None, nregistro=nr, tipos=["ipt"]))

    try:
        resultados_list = await asyncio.gather(*tasks)
    except ClientResponseError:
        raise HTTPException(status_code=502, detail="Error upstream descargando IPT")
    except Exception:
        raise HTTPException(status_code=500, detail="Error interno procesando IPT")

    # Aplanar listas de rutas
    all_paths: List[str] = [path for sub in resultados_list for path in sub]
    return all_paths

# ---------------------------------------------------------------------------
# 13 · Identificar medicamento en Presentaciones.xls
# ---------------------------------------------------------------------------
@app.get(
    "/identificar-medicamento",
    operation_id="identificar_medicamento",
    summary="Identifica hasta 10 presentaciones en base a CN, nregistro o nombre",
    description=constant.identificar_medicamento,
)
async def identificar_medicamento(
    nregistro: Optional[str] = Query(
        None,
        description="Número de registro AEMPS."
    ),
    cn: Optional[str] = Query(
        None,
        description="Código Nacional del medicamento."
    ),
    nombre: Optional[str] = Query(
        None,
        description="Nombre o parte de la presentación. Búsqueda parcial o difusa."
    ),
) -> List[dict[str, Any]]:
    df = app.state.df_presentaciones

    if nregistro:
        matches = df[df["Nº Registro"].astype(str) == nregistro]
    elif cn:
        matches = df[df["Cod. Nacional"].astype(str) == cn]
    elif nombre:
        norm_nombre = _normalize(nombre)
        opciones_norm = df["Presentación"].fillna("").apply(_normalize)
        df_aux = df.assign(_norm=opciones_norm)
        matches = df_aux[df_aux["_norm"].str.contains(norm_nombre)].drop(columns="_norm")
        if matches.empty:
            from difflib import get_close_matches
            pool = opciones_norm.tolist()
            similares = get_close_matches(norm_nombre, pool, n=10, cutoff=0.7)
            if similares:
                matches = (
                    df_aux[df_aux["_norm"].isin(similares)]
                    .assign(_order=lambda d: d["_norm"].apply(similares.index))
                    .sort_values("_order")
                    .drop(columns=["_norm", "_order"] )
                )
    else:
        raise HTTPException(status_code=400, detail="Debe indicar 'nregistro', 'cn' o 'nombre'.")

    if matches.empty:
        return []
    return matches.head(10).to_dict(orient="records")

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
