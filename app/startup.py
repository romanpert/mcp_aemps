# startup.py
from fastapi import FastAPI
from contextlib import asynccontextmanager
from pathlib import Path
import logging
import pandas as pd

from redis.asyncio import Redis
from fastapi_cache.backends.redis import RedisBackend
from fastapi_cache import FastAPICache
from fastapi_limiter import FastAPILimiter

from app.docs_utils import download_presentaciones, download_nomenclator_csv
from app.config import settings
import os

logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Iniciando lifespan de la aplicación")

    # 1) Descargar Presentaciones
    try:
        download_presentaciones()
        logger.info("Descargar Presentaciones.xls: OK")
    except Exception as exc:
        logger.error(f"Error al descargar Presentaciones.xls: {exc}", exc_info=True)
        raise RuntimeError(f"Error al descargar Presentaciones.xls: {exc}")

    # 2) Descargar Nomenclátor CSV (y capturar ruta real)
    try:
        nomenclator_path = download_nomenclator_csv(
            dest_dir=os.path.join(settings.data_dir, "documentacion")
        )
        logger.info(f"Descargar nomenclátor: OK → {nomenclator_path}")
    except Exception as exc:
        logger.error(f"Error al descargar nomenclátor: {exc}", exc_info=True)
        raise RuntimeError(f"Error al descargar nomenclátor: {exc}")

    # 3) Validar que el fichero Excel existe
    xls_file = Path(settings.data_dir) / "documentacion" / "Presentaciones.xls"
    if not xls_file.exists():
        logger.error(f"No se encontró Presentaciones.xls en: {xls_file}")
        raise RuntimeError(f"No se encontró Presentaciones.xls en: {xls_file}")

    # 4) Cargar DataFrames en el estado de la app
    try:
        df_presentaciones = pd.read_excel(xls_file)
        df_nomenclator   = pd.read_csv(nomenclator_path)
        app.state.df_presentaciones = df_presentaciones
        app.state.df_nomenclator   = df_nomenclator
        logger.info(
            f"DataFrames cargados: "
            f"{len(df_presentaciones)} filas en Presentaciones.xls, "
            f"{len(df_nomenclator)} filas en {os.path.basename(nomenclator_path)}"
        )
    except Exception as exc:
        logger.error(f"Error al leer ficheros: {exc}", exc_info=True)
        raise RuntimeError(f"Error al leer ficheros: {exc}")

    # 5) Inicialización de Redis, cache y rate limiter
    try:
        redis = Redis.from_url(
            settings.redis_url,
            encoding="utf-8",
            decode_responses=True
        )
        FastAPICache.init(RedisBackend(redis), prefix=settings.cache_prefix)
        await FastAPILimiter.init(redis)
        logger.info("Redis conectado y cache + rate limiter inicializados")
    except Exception as exc:
        logger.error(f"Error al conectar a Redis: {exc}", exc_info=True)
        raise RuntimeError(f"Error al conectar a Redis: {exc}")

    yield

    logger.info("Finalizando lifespan de la aplicación")
