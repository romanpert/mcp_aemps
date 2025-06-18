"""cima_client.py
====================
Cliente asíncrono minimalista para invocar **todas** las habilidades/endpoint
oficiales de la API REST de CIMA (AEMPS) y devolver la respuesta *raw* (dict,
list, str o None).

Cada endpoint está expuesto como función independiente para poder reutilizarla
fácilmente desde otros scripts o cuadernos Jupyter.

• Probado con Python 3.12, httpx 0.27   —   2025‑05‑04.

Ejemplo rápido (CLI):
    python -m asyncio -c "import asyncio, cima_raw_client as c; print(asyncio.run(c.medicamento(cn='608679')))"
"""
from __future__ import annotations
import base64
import asyncio
import json
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Literal, AsyncIterator, Union
from datetime import datetime, timezone, timedelta
from dateutil import parser
import aiohttp
from aiohttp import ClientResponseError, ClientSession
from fastapi import FastAPI, Query, HTTPException
import logging
import httpx
from httpx import HTTPStatusError
from PIL import Image

logger = logging.getLogger(__name__)

BASE_URL = "https://cima.aemps.es/cima/rest"
HTML_BASE_URL = "https://cima.aemps.es/cima"
TIMEOUT = httpx.Timeout(15)

TIPOS_PROBLEMA = {
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

# Mapas de tipos
_DOC_TYPE_MAP: dict[str, int] = {
    'ft':  1,
    'p':   2,
    'ipe': 3,   # el valor real que devuelve CIMA
    'ipt': 3,   # alias semántico para tu API
}
_IMG_FULL_TYPES = ['formafarmac', 'materialas']
_DEFAULT_HEADERS = {'User-Agent': 'Mozilla/5.0'}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _clean(params: Dict[str, Any] | None) -> Dict[str, Any] | None:
    """Elimina claves con valor `None` para formar el querystring."""
    if not params:
        return None
    return {k: v for k, v in params.items() if v is not None}


def _parse_fecha(valor):
    """
    Detecta si 'valor' es:
      - int/float o str de dígitos (ms UNIX): lo convierte a ISO8601 UTC
      - cualquier otra str: lo intenta parsear con dateutil
    Si falla, devuelve el valor original.
    """
    # Timestamp UNIX en ms
    if isinstance(valor, (int, float)) or (isinstance(valor, str) and valor.isdigit()):
        ms = int(valor)
        # Construimos siempre desde 1970-01-01
        epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
        try:
            # segundos = ms/1000
            dt = epoch + timedelta(milliseconds=ms)
            return dt.isoformat()
        except OverflowError:
            return valor

    # Cualquier otra cadena
    if isinstance(valor, str):
        try:
            dt = parser.parse(valor)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.isoformat()
        except (ValueError, parser.ParserError):
            return valor

    # Otros tipos (None, bool, etc.)
    return valor

async def _request(
    method: str,
    path: str,
    *,
    params: Dict[str, Any] | None = None,
    json_body: Any | None = None,
    client: httpx.AsyncClient | None = None,
) -> Any | None:
    """Lanza la petición y devuelve datos parseados o str si no es JSON."""
    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(timeout=TIMEOUT)

    try:
        resp = await client.request(method, f"{BASE_URL}/{path}", params=_clean(params), json=json_body)
        resp.raise_for_status()

        # Cuerpo vacío
        if not resp.content:
            return None

        # Intentamos JSON; si falla devolvemos text
        try:
            return resp.json()
        except (json.JSONDecodeError, ValueError):
            return resp.text
    finally:
        if owns_client:
            await client.aclose()

def _ensure_dir(path: Path) -> None:
    """Crea el directorio si no existe."""
    path.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# 1. Medicamentos
# ---------------------------------------------------------------------------
async def medicamentos(
    *,
    nombre: str | None = None,
    laboratorio: str | None = None,
    practiv1: str | None = None,
    practiv2: str | None = None,
    idpractiv1: str | None = None,
    idpractiv2: str | None = None,
    cn: str | None = None,
    atc: str | None = None,
    nregistro: str | None = None,
    npactiv: int | None = None,
    triangulo: int | None = None,
    huerfano: int | None = None,
    biosimilar: int | None = None,
    sust: int | None = None,
    vmp: str | None = None,
    comerc: int | None = None,
    autorizados: int | None = None,
    receta: int | None = None,
    estupefaciente: int | None = None,
    psicotropo: int | None = None,
    estuopsico: int | None = None,
    pagina: int | None = None,
) -> Any | None:
    """GET /medicamentos – Lista paginada con múltiples filtros."""
    return await _request(
        "GET",
        "medicamentos",
        params=locals(),
    )


async def medicamento(*, cn: str | None = None, nregistro: str | None = None) -> Any | None:
    """GET /medicamento – Ficha completa del medicamento (cn o nregistro)."""
    if not (cn or nregistro):
        raise ValueError("Se requiere 'cn' o 'nregistro'.")
    return await _request("GET", "medicamento", params=locals())


# ---------------------------------------------------------------------------
# 2. Búsqueda en ficha técnica
# ---------------------------------------------------------------------------
async def buscar_en_ficha_tecnica(reglas: list[dict[str, Any]]) -> Any | None:
    """POST /buscarEnFichaTecnica – Array de reglas: seccion, texto, contiene(0|1)."""
    if not reglas:
        raise ValueError("Debe proporcionar al menos una regla de búsqueda.")
    return await _request("POST", "buscarEnFichaTecnica", json_body=reglas)


# ---------------------------------------------------------------------------
# 3. Presentaciones
# ---------------------------------------------------------------------------
async def presentaciones(
    *,
    cn: str | None = None,
    nregistro: str | None = None,
    vmp: str | None = None,
    vmpp: str | None = None,
    idpractiv1: str | None = None,
    comerc: int | None = None,
    estupefaciente: int | None = None,
    psicotropo: int | None = None,
    estuopsico: int | None = None,
    pagina: int | None = None,
) -> Any | None:
    """GET /presentaciones – Listado de presentaciones."""
    return await _request("GET", "presentaciones", params=locals())


async def presentacion(cn: str) -> Any | None:
    """GET /presentacion/{cn} – Detalle de una presentación concreta."""
    return await _request("GET", f"presentacion/{cn}")


# ---------------------------------------------------------------------------
# 4. Descripción clínica (VMP/VMPP)
# ---------------------------------------------------------------------------
async def vmpp(
    *,
    practiv1: str | None = None,
    idpractiv1: str | None = None,
    dosis: str | None = None,
    forma: str | None = None,
    atc: str | None = None,
    nombre: str | None = None,
    modoArbol: int | None = None,
    pagina: int | None = None,
) -> Any | None:
    """GET /vmpp – Devuelve VMP/VMPP filtrados."""
    return await _request("GET", "vmpp", params=locals())


# ---------------------------------------------------------------------------
# 5. Maestras
# ---------------------------------------------------------------------------
async def maestras(
    *,
    maestra: int | None = None,
    nombre: str | None = None,
    id: str | None = None,
    codigo: str | None = None,
    estupefaciente: int | None = None,
    psicotropo: int | None = None,
    estuopsico: int | None = None,
    enuso: int | None = None,
    pagina: int | None = None,
) -> Any | None:
    """GET /maestras – Catálogos de laboratorios, ATC, formas, etc."""
    return await _request("GET", "maestras", params=locals())


# ---------------------------------------------------------------------------
# 6. Registro de cambios
# ---------------------------------------------------------------------------
async def registro_cambios(
    *,
    fecha: str | None = None,
    nregistro: list[str] | None = None,
    metodo: str = "GET",
) -> Any:
    # NO imponer fecha por defecto
    payload_or_params = {}
    if fecha is not None:
        payload_or_params["fecha"] = fecha
    if nregistro is not None:
        payload_or_params["nregistro"] = nregistro

    if metodo.upper() == "POST":
        return await _request("POST", "registroCambios", json_body=payload_or_params)
    else:
        return await _request("GET", "registroCambios", params=payload_or_params)

# ---------------------------------------------------------------------------
# 7 · Problemas de suministro (cliente)
# ---------------------------------------------------------------------------
async def psuministro(
    cn: str | None = None,
    pagina: int = 1,
    tamanioPagina: int = 10,
) -> dict | list:
    """
    Cliente asíncrono para la API de Problemas de suministro:
      - GET  /psuministro                → listado global (v1)
      - GET  /psuministro/v2/cn/{cn}     → detalle por Código Nacional (v2)
    """
    # Selección de ruta y parámetros
    if cn:
        path, params = f"psuministro/v2/cn/{cn}", None
    else:
        path, params = "psuministro", {"pagina": pagina, "tamanioPagina": tamanioPagina}

    url = f"{BASE_URL}/{path}"
    async with ClientSession() as session:
        try:
            async with session.get(url, params=params, headers={"Accept": "application/json"}) as resp:
                if resp.status == 400:
                    text = await resp.text()
                    raise ValueError(f"Parámetros inválidos: {text}")
                if resp.status == 404 and cn:
                    return []  # detalle CN no existe
                resp.raise_for_status()
                raw = await resp.json()
        except ClientResponseError as e:
            raise HTTPException(status_code=e.status, detail=str(e))

    def _enrich(item: dict) -> None:
        # 1) Detectar “sin problemas” o ausencia de tipo
        observ = item.get("observ", "").lower()
        tipo_code = item.get("tipoProblemaSuministro")
        if "sin problemas" in observ or tipo_code is None:
            item["tipoProblemaSuministro_descripcion"] = "No existen problemas detectados"
            item["fecha_inicio"] = None
            item["fecha_fin"] = None
        else:
            # 2) Mapeo normal contra TIPOS_PROBLEMA
            item["tipoProblemaSuministro_descripcion"] = TIPOS_PROBLEMA.get(
                tipo_code,
                "Desconocido"
            )
            # 3) Conversión de fechas (si vienen)
            if "fini" in item:
                item["fecha_inicio"] = _parse_fecha(item.pop("fini"))
            if "ffin" in item:
                item["fecha_fin"]    = _parse_fecha(item.pop("ffin"))

    # Si es detalle CN → dict único
    if cn:
        _enrich(raw)
        return raw

    # Si es listado global → dict con "resultados"
    resultados = raw.get("resultados", [])
    for elem in resultados:
        _enrich(elem)
    raw["resultados"] = resultados
    return raw


# ---------------------------------------------------------------------------
# 8. Documentos segmentados – Secciones
# ---------------------------------------------------------------------------
async def doc_secciones(
    tipo_doc: int,
    *,
    nregistro: str | None = None,
    cn:        str | None = None
) -> Any | None:
    """
    GET /docSegmentado/secciones/{tipo_doc}
    Devuelve los metadatos de las secciones disponibles para un tipo de documento y medicamento.

    Parámetros:
    - tipo_doc (int): Código de tipo de documento (1=Ficha Técnica, 2=Prospecto, 3–4 otros).
      Debe estar en el rango [1,4].
    - nregistro (str, opcional): Número de registro del medicamento.
    - cn (str, opcional): Código nacional del medicamento.

    Solo es obligatorio uno de (nregistro, cn).  
    Raise:
      ValueError: si no se proporciona ni nregistro ni cn.

    Retorna:
      lista de objetos con metadatos de sección (e.g. {"seccion": "4.2", "titulo": "...", "orden": 1})
      o None si no hay resultado.
    """
    if not (nregistro or cn):
        raise ValueError("Se requiere 'nregistro' o 'cn'.")
    return await _request(
        "GET",
        f"docSegmentado/secciones/{tipo_doc}",
        params=_clean({"nregistro": nregistro, "cn": cn})
    )

# ---------------------------------------------------------------------------
# 9. Documentos segmentados – Contenido (SOLUCIÓN FINAL)
# ---------------------------------------------------------------------------
async def doc_contenido(
    tipo_doc: int,
    *,
    nregistro: str | None = None,
    cn:        str | None = None,
    seccion:   str | None = None,
    format:    str = "json",
) -> Any | None:
    """
    Obtiene contenido de documentos segmentados
    - tipo_doc: 1 (Ficha técnica) o 2 (Prospecto)
    - format: "json" (default), "html" o "txt"
    """
    if not (nregistro or cn):
        raise ValueError("Se requiere 'nregistro' o 'cn'.")
    
    if tipo_doc not in [1, 2]:
        raise ValueError(f"tipo_doc debe ser 1 o 2, recibido: {tipo_doc}")

    params = _clean({
        "nregistro": nregistro,
        "cn":        cn,
        "seccion":   seccion,
    })

    print(f"Llamando API: docSegmentado/contenido/{tipo_doc}")
    print(f"Params: {params}")
    print(f"Formato solicitado: {format}")

    try:
        # 🔥 SOLUCIÓN: Llamar sin headers, obtener JSON por defecto
        result = await _request(
            method="GET",
            path=f"docSegmentado/contenido/{tipo_doc}",
            params=params,
        )
        
        # Si el formato solicitado no es JSON, necesitamos convertir
        if format == "html":
            # Si result es JSON con contenido HTML, extraerlo
            if isinstance(result, list) and result:
                # Concatenar todo el contenido HTML de las secciones
                html_content = ""
                for seccion in result:
                    if isinstance(seccion, dict) and 'contenido' in seccion:
                        html_content += seccion['contenido']
                return html_content
            elif isinstance(result, dict) and 'contenido' in result:
                return result['contenido']
            else:
                return str(result)
                
        elif format == "txt":
            # Convertir JSON a texto plano
            if isinstance(result, list) and result:
                txt_content = ""
                for seccion in result:
                    if isinstance(seccion, dict):
                        if 'titulo' in seccion:
                            txt_content += f"{seccion['titulo']}\n"
                        if 'contenido' in seccion:
                            # Remover tags HTML del contenido
                            import re
                            clean_content = re.sub('<[^<]+?>', '', seccion['contenido'])
                            txt_content += f"{clean_content}\n\n"
                return txt_content.strip()
            elif isinstance(result, dict) and 'contenido' in result:
                import re
                return re.sub('<[^<]+?>', '', result['contenido'])
            else:
                return str(result)
        
        # Para formato JSON, devolver tal como viene
        return result
        
    except Exception as e:
        print(f"Error en _request: {type(e).__name__}: {e}")
        raise

# ---------------------------------------------------------------------------
# 10. Notas de seguridad
# ---------------------------------------------------------------------------
async def notas(nregistro: str) -> Any | None:
    """
    GET /notas?nregistro={nregistro} – Listado de notas de seguridad
    GET /notas/{nregistro}             – Detalle de notas de seguridad
    """
    data = await _request("GET", "notas", params={"nregistro": nregistro})
    if data is None or (isinstance(data, dict) and not data):
        return await _request("GET", f"notas/{nregistro}")
    return data

# ---------------------------------------------------------------------------
# 11. Materiales informativos (client CIMA unificado)
# ---------------------------------------------------------------------------
async def materiales(nregistro: Union[str, List[str]]) -> Any | None:
    """
    - Si recibe un str, llama a GET /materiales?nregistro={nregistro} y,
      si no obtiene nada, GET /materiales/{nregistro}. Devuelve
      {'nregistro': ..., 'materiales': List[Material]} o None.
    - Si recibe lista, itera y devuelve List[{'nregistro', 'materiales'}] o None.
    """
    async def _fetch_one(nr: str) -> list | None:
        try:
            data = await _request("GET", "materiales", params={"nregistro": nr})
            if not data:  # si es None o lista vacía
                data = await _request("GET", f"materiales/{nr}")
            # Ahora data puede ser:
            #  - lista de Material (si el endpoint devolvió lista)
            #  - dict (si CIMA devuelve un único objeto)
            if isinstance(data, dict):
                # si viene nested en “materiales”
                if "materiales" in data and isinstance(data["materiales"], list):
                    return data["materiales"]
                # si es ya un Material, lo envuelvo en lista
                return [data]
            # si es lista, la devuelvo (ó None si vacía)
            return data or None
        except HTTPStatusError as e:
            if e.response.status_code == 404:
                return None
            raise

    # caso múltiple
    if isinstance(nregistro, list):
        tareas = [_fetch_one(nr) for nr in nregistro]
        respuestas = await asyncio.gather(*tareas, return_exceptions=True)
        resultados = []
        for nr, res in zip(nregistro, respuestas):
            if isinstance(res, Exception):
                # tratar el error si es necesario (o hacer skip)
                continue
            if res:
                resultados.append({"nregistro": nr, "materiales": res})
        return resultados or None

    # caso único
    mat = await _fetch_one(nregistro)
    return {"nregistro": nregistro, "materiales": mat} if mat else None


# ---------------------------------------------------------------------------
# 12. Recuperación de HTML completo (FT / Prospecto)
# ---------------------------------------------------------------------------

async def get_html(
    tipo: Literal["ft", "p"],
    nregistro: str,
    filename: str
) -> AsyncIterator[bytes]:
    """
    Streaming de bytes desde https://cima.aemps.es/cima/dochtml/{tipo}/{nregistro}/{filename},
    pero haciendo raise_for_status ANTES de devolver los datos.
    """
    url = f"{HTML_BASE_URL}/dochtml/{tipo}/{nregistro}/{filename}"
    client = httpx.AsyncClient(timeout=TIMEOUT, headers=_DEFAULT_HEADERS)
    try:
        resp = await client.get(url, follow_redirects=True)
        # lanzamos aquí la excepción si es 4xx o 5xx
        resp.raise_for_status()
        # sólo si OK, devolvemos el streaming
        async for chunk in resp.aiter_bytes():
            yield chunk
    finally:
        await client.aclose()

async def get_html_bytes(
    tipo: Literal["ft", "p"],
    nregistro: str,
    filename: str
) -> bytes:
    """
    Descarga completa en bytes desde https://cima.aemps.es/cima/dochtml/{tipo}/{nregistro}/{filename}
    """
    url = f"{HTML_BASE_URL}/dochtml/{tipo}/{nregistro}/{filename}"
    async with httpx.AsyncClient(timeout=TIMEOUT, headers=_DEFAULT_HEADERS) as client:
        resp = await client.get(url, follow_redirects=True)
        resp.raise_for_status()
        return resp.content

# ---------------------------------------------------------------------------
# 13. Descargar documentos (con opción only_url o texto + cleanup)
# ---------------------------------------------------------------------------
def extract_text_from_pdf(pdf_path: Path) -> str:
    """Extrae todo el texto de un PDF usando PyMuPDF."""
    import fitz  # PyMuPDF
    doc = fitz.open(str(pdf_path))
    text_blocks = []
    for page in doc:
        text_blocks.append(page.get_text())
    doc.close()
    return "\n".join(text_blocks)


async def download_docs(
    cn: str | None = None,
    nregistro: str | None = None,
    tipos: list[str] = ("ft", "p", "ipt"),
    base_dir: str = "data/pdf",
    timeout: int = 15,
    only_url: bool = False,          # si True devuelve solo URLs
    with_text: bool = False,         # si True descarga, extrae texto y borra PDF
) -> List[dict] | List[str]:
    """
    - only_url=True: devuelve List[str] de URLs oficiales.
    - with_text=True: descarga, extrae texto, borra el PDF y devuelve List[dict] con keys: url, text.
    - ambos flags False: descarga y devuelve List[str] de rutas locales.
    """
    med = await medicamento(cn=cn, nregistro=nregistro)
    if not isinstance(med, dict):
        return []

    docs = med.get("data", {}).get("docs") or med.get("docs") or []
    if not docs:
        return []

    # Sólo URLs
    if only_url:
        urls: List[str] = []
        for tipo in tipos:
            code = _DOC_TYPE_MAP.get(tipo.lower())
            if not code:
                continue
            for doc in docs:
                if doc.get("tipo") == code and doc.get("url"):
                    urls.append(doc["url"])
        return urls

    # Descarga y/o extracción de texto
    results = []
    async with httpx.AsyncClient(timeout=timeout) as client:
        for tipo in tipos:
            code = _DOC_TYPE_MAP.get(tipo.lower())
            if not code:
                continue

            dest_dir = Path(base_dir) / tipo.lower()
            dest_dir.mkdir(parents=True, exist_ok=True)

            for doc in docs:
                if doc.get("tipo") == code and doc.get("url"):
                    url = doc["url"]
                    resp = await client.get(url, follow_redirects=True)
                    resp.raise_for_status()

                    filename = Path(url).name
                    local_path = dest_dir / filename
                    local_path.write_bytes(resp.content)

                    if with_text:
                        # Extrae texto y borra el PDF local
                        text = extract_text_from_pdf(local_path)
                        results.append({"url": url, "text": text})
                        try:
                            local_path.unlink()
                        except Exception:
                            pass
                    else:
                        results.append(str(local_path))

    return results

# ---------------------------------------------------------------------------
# 13b. Descargar sólo IPT (envoltorio)
# ---------------------------------------------------------------------------
async def download_ipt(
    cn: str | None = None,
    nregistro: str | None = None,
    timeout: int = 15,
    only_url: bool = False,
    with_text: bool = False,
) -> List[dict] | List[str]:
    return await download_docs(
        cn=cn,
        nregistro=nregistro,
        tipos=["ipt"],
        base_dir="data/pdf/ipt",
        timeout=timeout,
        only_url=only_url,
        with_text=with_text,
    )

# ---------------------------------------------------------------------------
# 14. Función interna descargar_imagen con only_url y with_base64
# ---------------------------------------------------------------------------
# Tipos de imagen válidos
_VALID_IMAGE_TYPES = {"formafarmac", "materialas"}

async def descargar_imagen(
    cn: List[str] | None = None,
    nregistro: List[str] | None = None,
    tipos: List[str] = ("formafarmac", "materialas"),
    base_dir: str = "data/img",
    timeout: int = 15,
    only_url: bool = False,
    with_base64: bool = False,
) -> Dict[str, List[Union[str, Dict[str, Any]]]]:
    """
    Descarga imágenes y las agrupa por CN o NRegistro.

    - only_url=True,  with_base64=False: devuelve solo URLs (List[str] si solo url o List[dict]{'url'}).
    - only_url=False, with_base64=False: descarga localmente y devuelve rutas (List[str]).
    - only_url=False, with_base64=True: devuelve solo base64 (List[dict]{'base64'}).
    - only_url=True,  with_base64=True: devuelve url y base64 (List[dict]{'url','base64'}).
    """
    if not (cn or nregistro):
        return {}

    tipos_validos = [t.lower() for t in tipos if t.lower() in _VALID_IMAGE_TYPES]
    if not tipos_validos:
        return {}

    client = httpx.AsyncClient(timeout=timeout)
    resultados_por_code: Dict[str, List[Union[str, Dict[str, Any]]]] = {}

    async def _procesar_med(code: str, med: dict):
        fotos = med.get("data", {}).get("fotos", []) or med.get("fotos", [])
        lista_imagenes: List[Union[str, Dict[str, Any]]] = []
        for foto in fotos:
            tipo = foto.get("tipo")
            url_thumb = foto.get("url")
            if tipo in tipos_validos and url_thumb:
                url_full = url_thumb.replace("/thumbnails/", "/full/")

                # only_url sin base64: devolvemos solo URL
                if only_url and not with_base64:
                    lista_imagenes.append(url_full)
                    continue

                # descargamos el contenido
                resp = await client.get(url_full, follow_redirects=True)
                resp.raise_for_status()
                content = resp.content

                # solo local sin base64
                if not only_url and not with_base64:
                    dest_dir = Path(base_dir) / tipo
                    dest_dir.mkdir(parents=True, exist_ok=True)
                    local_path = dest_dir / Path(url_full).name
                    local_path.write_bytes(content)
                    lista_imagenes.append(str(local_path))
                    continue

                # codificar en base64 para los casos restantes
                b64 = base64.b64encode(content).decode("ascii")

                # only base64
                if not only_url and with_base64:
                    lista_imagenes.append({"base64": b64})
                # both url + base64
                elif only_url and with_base64:
                    lista_imagenes.append({"url": url_full, "base64": b64})

        resultados_por_code[code] = lista_imagenes

    for code in cn or []:
        med = await medicamento(cn=code)
        if isinstance(med, dict):
            await _procesar_med(code, med)

    for code in nregistro or []:
        med = await medicamento(nregistro=code)
        if isinstance(med, dict):
            await _procesar_med(code, med)

    await client.aclose()
    return resultados_por_code


# ---------------------------------------------------------------------------
# __main__ – demostración rápida (CLI)
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    async def demo():
        print(json.dumps(await medicamento(cn="608679"), indent=2, ensure_ascii=False)[:2000])

    # Maneja ejecución en entornos con loop activo (Jupyter) de forma segura
    try:
        asyncio.run(demo())
    except RuntimeError as exc:
        if "asyncio.run()" in str(exc):
            import nest_asyncio

            nest_asyncio.apply()
            asyncio.get_event_loop().run_until_complete(demo())
        else:
            raise
