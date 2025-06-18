# app/config.py
from pathlib import Path
from typing import List, Annotated
import os

from dotenv import load_dotenv
from pydantic import AnyUrl, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict, NoDecode

# 1) Carga el .env en memoria
ROOT_DIR = Path(__file__).parent.parent
load_dotenv(ROOT_DIR / ".env")

class Settings(BaseSettings):
    # Configuración de Pydantic v2
    model_config = SettingsConfigDict(
        case_sensitive=False,
    )

    # Versión de la aplicación
    mcp_version: str = Field("0.1.0", description="Versión del servidor")

    # Servidor
    uvicorn_host: str = Field("0.0.0.0", description="Host donde bindeará Uvicorn")
    access_host: str = Field("localhost", description="Host público para la API")
    port: int = Field(8000, description="Puerto TCP")

    # Redis (host, puerto, usuario y contraseña);
    # luego montamos redis_url automáticamente
    redis_host: str = Field("localhost", description="Host de Redis")
    redis_port: int = Field(6379, description="Puerto de Redis")
    redis_user: str = Field("default", description="Usuario Redis")
    redis_password: str = Field(..., description="Password Redis")
    redis_url: AnyUrl | None = Field(
        None,
        description="Cadena completa de conexión a Redis (se autogenera si no se provee)"
    )
    cache_prefix: str = Field("fastapi-cache", description="Prefijo de cache")

    # CORS
    allowed_origins: Annotated[List[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:3000"],
        description="Orígenes permitidos para CORS"
    )

    # Datos
    data_dir: str = Field("/data", description="Ruta montada con los datos")

    # Rate limiting
    rate_limit: int = Field(100, description="Peticiones por periodo")
    rate_period: int = Field(60, description="Periodo en segundos")

    @field_validator("allowed_origins", mode="before")
    def split_allowed_origins(cls, v):
        if isinstance(v, str):
            return [u.strip() for u in v.split(",") if u.strip()]
        return v
    
    @field_validator("redis_url", mode="before")
    def assemble_redis_url(cls, v, info):
        # si el usuario pasa REDIS_URL en .env, úsalo;
        # si no, métele host, port, user, password
        if v is not None:
            return v
        data = info.data
        user = data.get("redis_user")
        pwd  = data.get("redis_password")
        host = data.get("redis_host")
        port = data.get("redis_port")
        return f"redis://{user}:{pwd}@{host}:{port}/0"

    @field_validator("port")
    def port_must_be_valid(cls, v):
        if not (1 <= v <= 65535):
            raise ValueError("El puerto debe estar entre 1 y 65535")
        return v

    @field_validator("data_dir")
    def ensure_data_dir_exists(cls, v):
        p = Path(v)
        # Si no existe, lo creamos
        try:
            p.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            raise ValueError(f"No se pudo crear el directorio de datos '{v}': {e}")
        return str(p.resolve())

# Instanciamos
settings = Settings()