# app/startup.py
from fastapi import FastAPI
from contextlib import asynccontextmanager
from pathlib import Path
import logging
import pandas as pd
import asyncio

from starlette.concurrency import run_in_threadpool
from redis.asyncio import Redis
from fastapi_cache.backends.redis import RedisBackend
from fastapi_cache import FastAPICache
from fastapi_limiter import FastAPILimiter
from fastapi_cache.backends.inmemory import InMemoryBackend

from app.docs_utils import download_presentaciones, download_nomenclator_csv
from app.config import settings

logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Iniciando lifespan de la aplicación")

    data_dir = Path(settings.data_dir) / "documentacion"
    xls_path = data_dir / "Presentaciones.xls"
    csv_dir = data_dir

    # Descargar Presentaciones y CSV de Nomenclátor concurrentemente
    try:
        downloaded_xls, downloaded_csv = await asyncio.gather(
            download_presentaciones(xls_path, timeout=60), # settings.timeout
            download_nomenclator_csv(csv_dir, timeout=60), # settings.timeout
        )
        logger.debug(
            f"Descargas completadas: {downloaded_xls} ({downloaded_xls.stat().st_size} bytes), "
            f"{downloaded_csv} ({downloaded_csv.stat().st_size} bytes)"
        )
    except Exception as exc:
        logger.error(f"Error en descargas iniciales: {exc}", exc_info=True)
        raise RuntimeError(f"Error en descargas: {exc}")

    # Cargar DataFrames en hilos separados para no bloquear el event loop
    try:
        df_presentaciones, df_nomenclator = await asyncio.gather(
            run_in_threadpool(pd.read_excel, downloaded_xls),
            run_in_threadpool(pd.read_csv, downloaded_csv),
        )
        app.state.df_presentaciones = df_presentaciones
        app.state.df_nomenclator = df_nomenclator
        logger.debug(
            f"DataFrames cargados: {len(df_presentaciones)} filas en Presentaciones.xls, "
            f"{len(df_nomenclator)} filas en nomenclátor.csv"
        )
    except Exception as exc:
        logger.error(f"Error al leer ficheros: {exc}", exc_info=True)
        raise RuntimeError(f"Error al leer ficheros: {exc}")

    # Inicialización de Redis o caché en memoria
    if settings.redis_url:
        try:
            redis = Redis.from_url(settings.redis_url, encoding="utf-8", decode_responses=True)
            FastAPICache.init(RedisBackend(redis), prefix=settings.cache_prefix)
            await FastAPILimiter.init(
                redis,
                prefix="mcp_rl:"       # prefijo en Redis para distinguir tus llaves
            )
            app.state.redis = redis    # opcional, para usarlo en middleware
            logger.info("Redis conectado: cache y rate limiter inicializados")
        except Exception as exc:
            logger.warning(
                f"No se pudo inicializar Redis: {exc}. Usando caché en memoria y sin limitador."
            )
            FastAPICache.init(InMemoryBackend(), prefix="inmemory")
    else:
        logger.info("settings.redis_url vacío: usando caché en memoria sin limitador")
        FastAPICache.init(InMemoryBackend(), prefix="inmemory")

    yield

    logger.info("Finalizando lifespan de la aplicación")