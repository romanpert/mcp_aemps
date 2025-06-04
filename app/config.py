# config.py
from pathlib import Path
import os
import json
from typing import List
from pydantic import BaseModel, AnyUrl, Field, FieldValidationInfo, field_validator

BASE_DIR = Path(__file__).parent
CONFIG_FILE = BASE_DIR / "mcp_aemps.json"

if not CONFIG_FILE.exists():
    raise RuntimeError(f"No se encontró el fichero de configuración: {CONFIG_FILE}")

with open(CONFIG_FILE, "r", encoding="utf-8") as f:
    _cfg_data = json.load(f)


class Settings(BaseModel):
    uvicorn_host: str = Field(default=_cfg_data.get("uvicorn_host", "0.0.0.0"))
    access_host:  str = Field(default=_cfg_data.get("access_host", "localhost"))
    port: int         = Field(default=_cfg_data.get("port", 8000))
    redis_url: AnyUrl = Field(default=_cfg_data.get("redis_url", "redis://localhost:6379/0"))
    allowed_origins: List[str] = Field(default=_cfg_data.get("allowed_origins", ["http://localhost:3000"]))
    cache_prefix: str  = Field(default=_cfg_data.get("cache_prefix", "fastapi-cache"))
    data_dir: str      = Field(default=_cfg_data.get("data_dir", str(BASE_DIR / "data")))

    class Config:
        # Asignamos nombres de variable de entorno a cada campo
        fields = {
            "uvicorn_host": {"env": "UVICORN_HOST"},
            "access_host": {"env": "ACCESS_HOST"},
            "port": {"env": "PORT"},
            "redis_url": {"env": "REDIS_URL"},
            "allowed_origins": {"env": "ALLOWED_ORIGINS"},
            "cache_prefix": {"env": "CACHE_PREFIX"},
            "data_dir": {"env": "DATA_DIR"},
        }

    @field_validator("allowed_origins", mode="before")
    @classmethod
    def split_allowed_origins(cls, v: str | List[str], info: FieldValidationInfo) -> List[str]:
        """
        Si ALLOWED_ORIGINS se pasa como cadena de texto separada por comas, la convertimos a lista.
        """
        if isinstance(v, str):
            return [origin.strip() for origin in v.split(",") if origin.strip()]
        return v  # Ya es lista

    @field_validator("port")
    @classmethod
    def port_must_be_positive(cls, v: int, info: FieldValidationInfo) -> int:
        if v <= 0 or v > 65535:
            raise ValueError("El puerto debe estar entre 1 y 65535")
        return v

    @field_validator("data_dir")
    @classmethod
    def data_dir_must_exist(cls, v: str, info: FieldValidationInfo) -> str:
        p = Path(v)
        if not p.exists() or not p.is_dir():
            raise ValueError(f"El directorio de datos no existe o no es carpeta: {v}")
        return v


# Instanciamos y validamos
settings = Settings()

# Tras la validación, revisamos que DATA_DIR realmente exista
data_path = Path(settings.data_dir)
if not data_path.exists():
    raise RuntimeError(f"El directorio de datos no existe: {data_path}")
