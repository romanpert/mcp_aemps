# dependencies.py
import logging
from fastapi import Depends, HTTPException
from fastapi.security import OAuth2PasswordBearer

logger = logging.getLogger(__name__)
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/token")


async def get_current_user(token: str = Depends(oauth2_scheme)):
    """
    Dependencia que valida un token OAuth2 (JWT).
    En esta fase beta, simplemente comprobamos que venga un token no vacío.
    En producción habría que verificar firma, expiración y scopes.
    """
    if not token:
        logger.warning("No se proporcionó token de autenticación")
        raise HTTPException(status_code=401, detail="No autenticado")
    # TODO: validar JWT (firma, expiración, scopes…)
    # Por ahora devolvemos un placeholder
    return {"sub": "usuario_demo"}

