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

import asyncio
import json
from datetime import date
from pathlib import Path
from typing import Any, Dict, Optional, Literal, AsyncIterator
from datetime import datetime, timezone
from dateutil import parser
import aiohttp
from aiohttp import ClientResponseError, ClientSession

import httpx
from PIL import Image

BASE_URL = "https://cima.aemps.es/cima/rest"
TIMEOUT = httpx.Timeout(15)

TIPOS_PROBLEMA = {
    1: "Consultar Nota Informativa",
    2: "Suministro sólo a hospitales.",
    3: "El médico prescriptor deberá determinar la posibilidad de utilizar otros tratamientos comercializados.",
    4: "Desabastecimiento temporal.",
    5: "Existe/n otro/s medicamento/s con el mismo principio activo y para la misma vía de administración.",
    6: "Existe/n otro/s medicamento/s con los mismos principios activos y para la misma vía de administración.",
    7: "Se puede solicitar como medicamento extranjero.",
    8: "Se recomienda restringir su prescripción reservándolo para casos en que no exista una alternativa apropiada.",
    9: "El titular de autorización de comercialización está realizando una distribución controlada al existir unidades limitadas."
}

# Mapas de tipos
_DOC_TYPE_MAP = {'ft': 1, 'p': 2, 'ipt': 3}
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
    Convierte:
     - int/float (segundos o milisegundos UNIX)
     - str numérico (segundos o milisegundos)
     - str ISO
    a ISO 8601 UTC.
    """
    if isinstance(valor, (int, float)) or (isinstance(valor, str) and valor.isdigit()):
        v = int(valor)
        # Si es un timestamp en milisegundos (>10 dígitos), dividimos
        if v > 1e10:
            ts = v / 1000
        else:
            ts = v
        return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()

    if isinstance(valor, str):
        try:
            dt = parser.parse(valor)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.isoformat()
        except Exception:
            return valor
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
    nregistro: str | None = None,
    metodo: str = "GET",  # Puede ser "GET" o "POST" según se prefiera
) -> Any | None:
    """GET/POST /registroCambios – Seguimiento de altas/bajas/modificaciones."""
    if fecha is None:
        fecha = date.today().strftime("%d/%m/%Y")
    if metodo.upper() == "POST":
        return await _request("POST", "registroCambios", json_body={"fecha": fecha, "nregistro": nregistro})
    return await _request("GET", "registroCambios", params={"fecha": fecha, "nregistro": nregistro})

# ---------------------------------------------------------------------------
# 7 · Problemas de suministro (cliente)
# ---------------------------------------------------------------------------
async def psuministro(cn: str | None = None) -> dict | list:
    """
    GET /psuministro           ← listado global (v1)
    GET /psuministro/v2/cn/{cn} ← detalle por Código Nacional (v2)

    Códigos HTTP:
      - 400: parámetros no válidos → ValueError
      - 404: CN no existe → devuelve []
    Fechas en POSIX (segundos) parseadas a ISO 8601 UTC.
    """
    base_v2 = "psuministro/v2"
    base_v1 = "psuministro"

    if cn is None:
        path, params = base_v1, {"pagina": 1, "tamanioPagina": 20}
    else:
        path, params = f"{base_v2}/cn/{cn}", None

    url = f"{BASE_URL}/{path}"
    async with ClientSession() as session:
        try:
            async with session.get(url, params=params,
                                   headers={"Accept": "application/json"}) as resp:
                if resp.status == 400:
                    # Parámetros mal formados
                    text = await resp.text()
                    raise ValueError(f"Parámetros inválidos: {text}")
                if resp.status == 404:
                    # CN no existe
                    return []
                resp.raise_for_status()
                raw = await resp.json()
        except ClientResponseError as e:
            # Otros errores HTTP
            raise

    # Normalizar a lista
    if isinstance(raw, dict) and "resultados" in raw:
        items = raw["resultados"]
        wrap = False
    else:
        items = raw if isinstance(raw, list) else [raw]
        wrap = True

    # Post-procesado
    for item in items:
        codigo = item.get("tipoProblemaSuministro")
        item["tipoProblemaSuministro_descripcion"] = TIPOS_PROBLEMA.get(codigo, "Desconocido")
        if "fini" in item:
            item["fecha_inicio"] = _parse_fecha(item.pop("fini"))
        if "ffin" in item:
            item["fecha_fin"]    = _parse_fecha(item.pop("ffin"))

    if wrap:
        return items
    raw["resultados"] = items
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
# 9. Documentos segmentados – Contenido
# ---------------------------------------------------------------------------
async def doc_contenido(
    tipo_doc: int,
    *,
    nregistro: str | None = None,
    cn:        str | None = None,
    seccion:   str | None = None
) -> Any | None:
    """
    GET /docSegmentado/contenido/{tipo_doc}
    Devuelve el contenido (HTML o JSON) de las secciones de un documento.

    Parámetros:
    - tipo_doc (int): Código de tipo de documento (1=Ficha Técnica, 2=Prospecto, 3–4 otros).
      Debe estar en el rango [1,4].
    - nregistro (str, opcional): Número de registro del medicamento.
    - cn (str, opcional): Código nacional del medicamento.
    - seccion (str, opcional): Identificador de sección (e.g. "4.2"). Si se omite,
      devuelve todas las secciones.

    Solo es obligatorio uno de (nregistro, cn).  
    Raise:
      ValueError: si no se proporciona ni nregistro ni cn.

    Retorna:
      lista de objetos con contenido de sección (e.g. {"seccion": "4.2", "html": "..."})
      o None si no hay resultado.
    """
    if not (nregistro or cn):
        raise ValueError("Se requiere 'nregistro' o 'cn'.")
    return await _request(
        "GET",
        f"docSegmentado/contenido/{tipo_doc}",
        params=_clean({"nregistro": nregistro, "cn": cn, "seccion": seccion})
    )

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
# 11. Materiales informativos
# ---------------------------------------------------------------------------
async def materiales(nregistro: str) -> Any | None:
    """
    GET /materiales?nregistro={nregistro} – Listado de materiales (docs, vídeos)
    GET /materiales/{nregistro}             – Detalle de materiales informativos
    """
    data = await _request("GET", "materiales", params={"nregistro": nregistro})
    if data is None or (isinstance(data, dict) and not data):
        return await _request("GET", f"materiales/{nregistro}")
    return data

# ---------------------------------------------------------------------------
# 12. Recuperación de HTML completo (FT / Prospecto)
# ---------------------------------------------------------------------------
async def get_html(
    tipo: Literal["ft", "p"],
    nregistro: str,
    filename: str
) -> AsyncIterator[bytes]:
    """
    Stream del HTML completo de:
      • Ficha técnica:  GET /dochtml/ft/{nregistro}/FichaTecnica.html  
      • Prospecto   :  GET /dochtml/p/{nregistro}/Prospecto.html  
    También admite rutas con sección: 
      /dochtml/{tipo}/{nregistro}/{seccion}/{filename}
    """
    path = f"dochtml/{tipo}/{nregistro}/{filename}"
    url = f"{BASE_URL}/{path}"
    client = httpx.AsyncClient(timeout=TIMEOUT, headers=_DEFAULT_HEADERS)
    try:
        resp = await client.get(url, follow_redirects=True)
        resp.raise_for_status()
        async for chunk in resp.aiter_bytes():
            yield chunk
    finally:
        await client.aclose()

# ---------------------------------------------------------------------------
# 13. Descargar documentos
# ---------------------------------------------------------------------------
async def download_docs(
    cn: str | None = None,
    nregistro: str | None = None,
    tipos: list[str] = ['ft', 'p', 'ipt'],
    base_dir: str = 'data/pdf',
    timeout: int = 15,
) -> list[str]:
    """
    Tool: Descarga PDFs de CIMA.
    Args:
      cn: Código Nacional.
      nregistro: Número de registro.
      tipos: ['ft','p','ipt'].
      base_dir: Carpeta raíz para guardar.
      timeout: segundos de timeout HTTP.
    Returns:
      Lista de rutas de archivos descargados.
    """
    med = await medicamento(cn=cn, nregistro=nregistro)
    if not med or not isinstance(med, dict):
        return []
    docs = med.get('docs') or []
    if not docs:
        return []

    client = httpx.AsyncClient(timeout=httpx.Timeout(timeout), headers=_DEFAULT_HEADERS)
    downloaded = []
    try:
        for tipo in tipos:
            code = _DOC_TYPE_MAP.get(tipo.lower())
            if not code:
                continue
            dest_dir = Path(base_dir) / tipo.lower()
            _ensure_dir(dest_dir)
            for doc in docs:
                if doc.get('tipo') == code and doc.get('url'):
                    url = doc['url']
                    resp = await client.get(url, follow_redirects=True)
                    resp.raise_for_status()
                    filepath = dest_dir / Path(url).name
                    filepath.write_bytes(resp.content)
                    downloaded.append(str(filepath))
    finally:
        await client.aclose()
    return downloaded

# ---------------------------------------------------------------------------
# 13b. Descargar solo IPT
# ---------------------------------------------------------------------------
async def download_ipt(
    cn: str | None = None,
    nregistro: str | None = None,
    base_dir: str = 'data/pdf/ipt',
    timeout: int = 15,
) -> list[str]:
    """
    Envuelve download_docs para descargar únicamente el Informe de Posicionamiento Terapéutico.
    """
    # reutiliza la lógica de download_docs con tipos=['ipt']
    return await download_docs(
        cn=cn,
        nregistro=nregistro,
        tipos=['ipt'],
        base_dir=base_dir,
        timeout=timeout,
    )
