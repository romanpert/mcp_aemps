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

from app.docs_utils import download_presentaciones
from app.config import settings

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Iniciando lifespan de la aplicación")

    # 1) Descargar presentaciones (si no están en disco)
    try:
        download_presentaciones()
        logger.info("download_presentaciones(): OK")
    except Exception as exc:
        logger.error(f"Error al descargar presentaciones: {exc}", exc_info=True)
        raise RuntimeError(f"Error al descargar presentaciones: {exc}")

    # 2) Validar que el fichero Excel existe
    xls_file = Path(settings.data_dir) / "documentacion" / "Presentaciones.xls"
    if not xls_file.exists():
        logger.error(f"No se encontró Presentaciones.xls en: {xls_file}")
        raise RuntimeError(f"No se encontró Presentaciones.xls en: {xls_file}")

    # 3) Cargar DataFrame
    try:
        df_presentaciones = pd.read_excel(xls_file)
        app.state.df_presentaciones = df_presentaciones
        logger.info(f"DataFrame cargado: {len(df_presentaciones)} filas en Presentaciones.xls")
    except Exception as exc:
        logger.error(f"Error al leer Presentaciones.xls: {exc}", exc_info=True)
        raise RuntimeError(f"Error al leer Presentaciones.xls: {exc}")

    # 4) Conectar a Redis e inicializar cache y limiter
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

    # Yield: la aplicación ya puede recibir peticiones
    yield

    # Código final tras shutdown (opcional: cerrar conexiones si hiciera falta)
    logger.info("Finalizando lifespan de la aplicación")
