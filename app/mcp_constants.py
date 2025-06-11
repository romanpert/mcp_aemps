# ---------------------------------------------------------------------------
#   Constantes internas
# ---------------------------------------------------------------------------
_IMG_FULL_TYPES = ['formafarmac', 'materialas']
_DOC_TYPE_MAP   = {'ft':  1, 'p': 2, 'ipt': 3}

# ---------------------------------------------------------------------------
# Prompt helper
# ---------------------------------------------------------------------------
MCP_AEMPS_SYSTEM_PROMPT = (
    """
Eres un **agente farmacéutico digital** en España con acceso a las siguientes herramientas MCP sobre la API CIMA (AEMPS):

1. **Obtener ficha de un medicamento**  
   • `obtener_medicamento(cn, nregistro)`  
   - Parámetros: `cn` (Código Nacional) o `nregistro` (Número de registro).  
   - Devuelve: ficha completa con dosis, forma, vía, estado comercial, fechas y alertas.

2. **Listar y filtrar medicamentos**  
   • `buscar_medicamentos(**filtros)`  
   - Parámetros opcionales: `nombre`, `laboratorio`, `practiv1`, `practiv2`, `atc`, `cn`, `nregistro`, `huerfano`, `biosimilar`, `triangulo`, `pagina`, etc.  
   - Devuelve: listado paginado con más de 20 posibles filtros.

3. **Buscar en ficha técnica**  
   • `buscar_en_ficha_tecnica(reglas)`  
   - Cuerpo: lista de reglas `{seccion, texto, contiene}`.  
   - Devuelve: coincidencias dentro de secciones específicas.

4. **Presentaciones de un medicamento**  
   • `listar_presentaciones(cn, nregistro, vmp, vmpp, idpractiv1, pagina, ...)`  
   • `obtener_presentacion(cn=[...])`  
   - `listar_presentaciones`: listado general.  
   - `obtener_presentacion`: detalle para uno o varios CN (paraleliza llamadas y devuelve `{cn: detalle}`).

5. **Equivalentes clínicos (VMP/VMPP)**  
   • `buscar_vmpp(practiv1, dosis, forma, atc, nombre, modoArbol, pagina)`  
   - Filtra por principio activo, dosis, forma farmacéutica, ATC, etc.

6. **Catálogos maestros**  
   • `consultar_maestras(maestra, nombre, id, codigo, estupefaciente, psicotropo, enuso, pagina)`  
   - Acceso a ATC, principios activos, formas farmacéuticas, laboratorios…

7. **Registro de cambios**  
   • `registro_cambios(fecha="dd/mm/yyyy", nregistro, metodo="GET"|"POST")`  
   - Historial de altas, bajas y modificaciones desde una fecha dada.

8. **Problemas de suministro**  
   • `problemas_suministro(cn=[...])`  
   - Sin parámetros: paginado global.  
   - Con uno o varios CN: paraleliza llamadas y devuelve `{cn: resultado}`.

9. **Documentos segmentados**  
   • `doc_secciones(tipo_doc=1-4, nregistro, cn)` → metadatos de secciones.  
   • `doc_contenido(tipo_doc=1-4, nregistro, cn, seccion)` → contenido HTML/JSON de cada sección.

10. **Notas de seguridad**  
    • `listar_notas(nregistro=[...])`  
    • `obtener_notas(nregistro)`  
    - Soporta uno o varios números de registro, devuelve lista o `{nregistro: notas}`.

11. **Materiales informativos**  
    • `listar_materiales(nregistro=[...])`  
    • `obtener_materiales(nregistro)`  
    - Igual que notas, para materiales informativos.

12. **Descarga de HTML completo**  
    • Ficha técnica:  
      - `html_ficha_tecnica_multiple(nregistro=[...], filename)`  
      - `html_ficha_tecnica(nregistro, filename)`  
    • Prospecto:  
      - `html_prospecto_multiple(nregistro=[...], filename)`  
      - `html_prospecto(nregistro, filename)`  
    - Para varios registros devuelve `{nregistro: html_str}`, para uno StreamingResponse.

13. **Descargar Informe de Posicionamiento Terapéutico (IPT)**  
    • `descargar_ipt(cn=[...], nregistro=[...])`  
    - Devuelve lista de rutas de archivos IPT, aplana resultados de múltiples llamadas.

14. **Identificar medicamento en Presentaciones.xls**  
    • `identificar_medicamento(nregistro, cn, nombre)`  
    - Busca en el Excel, normaliza texto y, si no hay coincidencia, usa similitud difusa para devolver hasta 10 resultados.

---
## Flujo recomendado

1. Para ficheros o imágenes, primero usa **`descargar_documentos`** o **`descargar_imagenes`** (herramientas MCP genéricas).  
2. Para datos estructurados, emplea la herramienta específica (por ejemplo, `obtener_medicamento`, `listar_presentaciones`, etc.).  
3. Para contenido segmentado, usa `doc_secciones` y `doc_contenido`.  
4. Para búsquedas de texto, usa `buscar_en_ficha_tecnica`.  
5. Para listados con filtros, usa `buscar_medicamentos` o `buscar_vmpp`.

---
## Pautas para las respuestas

- Resume siempre: **dosis, forma, vía**, **estado comercial**, **fechas** relevantes y **alertas** principales.  
- No proporciones consejo médico; solo información regulatoria.  
- **Cita “Datos CIMA (AEMPS)”** cada vez que extraigas datos de las herramientas, así como las URLs HTTP que uses para consultar.  
- Incluye siempre la última fecha de actualización, p. ej., “Datos extraídos el 15/09/2024.”  
- Al final de cada respuesta, agrega una pequeña línea con el descargo de responsabilidad:  
  > Esta información no constituye consejo médico; se proporciona únicamente a efectos informativos. Datos proporcionados por la AEMPS.”  
- Maneja errores devolviendo mensajes claros si falta un parámetro obligatorio (por ejemplo, `cn` o `nregistro`), o si una herramienta upstream falla.  
- Asegúrate de no violar ningún término de uso de la AEMPS.  
"""
)

medicamento_description = """
Devuelve la **ficha completa** de un medicamento concreto,  
identificado por su Código Nacional (`cn`) o por su Número de Registro AEMPS (`nregistro`).

**Uso**:
- Si conoces el Código Nacional (solo dígitos), pásalo en `cn`.  
- Si conoces el Número de Registro AEMPS (solo dígitos), pásalo en `nregistro`.  
- No es necesario aportar ambos; basta uno de los dos.

**Respuesta**:  
Objeto JSON con la ficha completa del medicamento y metadatos asociados  
(descargo de responsabilidad, fecha de obtención de la información).

**Códigos de respuesta**:
- **200 OK**: Información encontrada.  
- **400 Bad Request**: No se ha proporcionado `cn` ni `nregistro`.  
- **404 Not Found**: No existe ningún medicamento con los datos indicados.
"""

medicamentos_description = """
Devuelve un **listado paginado** de medicamentos que cumplen los filtros especificados.

**Uso**:
- Proporciona uno o varios parámetros para filtrar la búsqueda.  
- Si no se especifica ningún filtro, se listan **todos** los medicamentos (paginados).  
- El parámetro `pagina` indica la página de resultados (entero ≥ 1; por defecto 1).

**Parámetros disponibles** (todos opcionales):
- `nombre` (str): nombre (coincidencia parcial o exacta).  
- `laboratorio` (str): laboratorio fabricante.  
- `practiv1`, `practiv2` (str): nombre del principio activo principal o secundario.  
- `idpractiv1`, `idpractiv2` (str): ID numérico del principio activo (solo dígitos).  
- `cn` (str): Código Nacional (solo dígitos).  
- `atc` (str): código ATC o descripción parcial.  
- `nregistro` (str): Número de Registro AEMPS (solo dígitos).  
- `npactiv` (int): número de principios activos asociados.  
- `triangulo`, `huerfano`, `biosimilar`, `comerc`, `autorizados`, `receta`, `estupefaciente`, `psicotropo`, `estuopsico` (int; 0 o 1): flags específicos.  
- `sust` (int; 1–5): tipo especial de medicamento.  
- `vmp` (str): ID de código VMP para equivalentes clínicos.  
- `pagina` (int; ≥1): página de resultados.

**Respuesta**:  
JSON con dos claves:
1. `resultados`: lista de ficheras de medicamentos.  
2. `meta`:  
   - `datos_obtenidos`: “Datos CIMA (AEMPS) extraídos el DD/MM/AAAA.”  
   - `descargo`: “Esta información no constituye consejo médico…”.
   - otros campos de utilidad.

**Códigos HTTP**:
- **200 OK**: listado devuelto con éxito.  
- **400 Bad Request**: parámetro fuera de rango o tipo inválido.  
- **404 Not Found**: no hay resultados para los filtros indicados.  
- **502 Bad Gateway**: error en la API externa CIMA.  
- **500 Internal Server Error**: error interno del servidor.
"""


buscar_ficha_tecnica_description = """
Realiza búsquedas textuales dentro de secciones concretas de la ficha técnica de uno o varios medicamentos.

**Uso**:
- Envía en el cuerpo un array JSON de reglas de búsqueda.  
- Cada regla (objeto) debe tener:
  - `seccion` (str): sección de la ficha técnica en formato “N” o “N.N” (p. ej. “4.1”).  
  - `texto` (str): palabra o frase a buscar.  
  - `contiene` (int): 1 = debe contener ese texto; 0 = no debe contenerlo.

**Ejemplo de cuerpo**:
[
  { "seccion": "4.1", "texto": "cáncer",   "contiene": 1 },
  { "seccion": "4.1", "texto": "estómago", "contiene": 0 }
]

Respuesta (200 OK):
Objeto JSON con:
  1. resultados: lista de coincidencias encontradas.

  2. meta:
    -datos_obtenidos: “Datos CIMA (AEMPS) extraídos el DD/MM/AAAA.”
    -descargo: “Esta información no constituye consejo médico; se proporciona solo a efectos informativos.”
    -otros campos de utilidad.

  3. Códigos HTTP:
    200 OK: búsqueda completada con éxito.
    400 Bad Request: cuerpo inválido, no es un array de reglas, falta algún campo o contiene no es 0/1.
    404 Not Found: no hay ninguna ficha técnica que cumpla las reglas indicadas.
"""

presentaciones_description = """
Devuelve un **listado paginado** de presentaciones de medicamentos según filtros opcionales.

**Uso**:
- Envia los filtros como parámetros de consulta.  
- Si no se especifica ningún filtro, se listan **todas** las presentaciones (paginadas).  
- `pagina` indica la página de resultados (entero ≥ 1; por defecto 1).

**Parámetros disponibles** (todos opcionales):
- `cn` (str): Código Nacional (solo dígitos).  
- `nregistro` (str): Número de registro AEMPS (solo dígitos).  
- `vmp`    (str): ID del código VMP para equivalentes clínicos.  
- `vmpp`   (str): ID del código VMPP.  
- `idpractiv1` (str): ID numérico del principio activo (solo dígitos).  
- `comerc`      (int; 0 o 1): 1 = comercializado, 0 = no comercializado.  
- `estupefaciente`, `psicotropo`, `estuopsico` (int; 0 o 1): flags de inclusión/exclusión.  
- `pagina`      (int; ≥ 1): página de resultados (por defecto 1).

**Respuesta** (200 OK):  
JSON con:
1. `resultados`: lista de presentaciones que cumplen los filtros.  
2. `meta`:  
   - `datos_obtenidos`: “Datos CIMA (AEMPS) extraídos el DD/MM/AAAA.”  
   - `descargo`: “Esta información no constituye consejo médico; se proporciona solo a efectos informativos.”

**Códigos HTTP**:
- **200 OK**: listado devuelto con éxito.  
- **400 Bad Request**: parámetro fuera de rango o tipo inválido.  
- **404 Not Found**: no hay presentaciones que cumplan los filtros indicados.  
"""

presentacion_description = """
Obtiene los detalles de presentación para uno o varios medicamentos identificados por su Código Nacional (CN).

**Uso**:
- Envia uno o varios `cn` como parámetros de consulta:  
  - Para un único CN: `GET /presentacion?cn=123456789`  
  - Para varios CN: `GET /presentacion?cn=123&cn=456&cn=789`

**Parámetro**:
- `cn` (List[str], requerido): uno o varios Códigos Nacionales (solo dígitos). Repetir `cn` por cada valor.

**Respuesta** (200 OK):
- **Caso único** (`len(cn) == 1`): devuelve directamente un objeto con:
  - `cn` (Código Nacional)  
  - `nregistro` (Número de registro AEMPS)  
  - `forma` (forma farmacéutica)  
  - `dosis` (dosificación)  
  - `laboratorio` (laboratorio fabricante)  
  - `meta`:  
    - `datos_obtenidos`: “Datos CIMA (AEMPS) extraídos el DD/MM/AAAA.”  
    - `descargo`: “Esta información no constituye consejo médico; se proporciona solo a efectos informativos.”

- **Caso múltiple** (`len(cn) > 1`): devuelve un objeto mapeando cada CN a su detalle (incluyendo `meta`), por ejemplo:
  {
    "123": { …detalle… },
    "456": { …detalle… },
    "errors": {
      "789": { "status_code": 404, "detail": "No encontrado" }
    }
  }

Códigos HTTP:
  200 OK: petición procesada con éxito (total o parcialmente).
  400 Bad Request: no se ha proporcionado ningún cn.
  404 Not Found: en el caso único, si no existe el CN; en el múltiple, si fallan todos los CN.
  502 Bad Gateway: error al obtener datos de la API externa.
  500 Internal Server Error: error interno al procesar la petición.
"""

vmpp_description = """
Devuelve un **listado (paginado)** de equivalentes clínicos VMP/VMPP según filtros opcionales.

**Uso**:
- Envía los filtros como parámetros de consulta.  
- Si no se especifica ningún filtro, devolverá todos los registros (si la API los soporta).  
- `pagina` indica la página de resultados (entero ≥ 1; por defecto 1).

**Parámetros disponibles** (todos opcionales):
- `practiv1`   (str): nombre del principio activo principal.  
- `idpractiv1` (str): ID numérico del principio activo (solo dígitos).  
- `dosis`      (str): dosis del medicamento (según CIMA).  
- `forma`      (str): forma farmacéutica.  
- `atc`        (str): código ATC o descripción parcial.  
- `nombre`     (str): nombre del medicamento.  
- `modoArbol`  (int): si se incluye (cualquier valor), devuelve la respuesta en modo jerárquico.  
- `pagina`     (int; ≥ 1): número de página de resultados.

**Respuesta** (200 OK):  
Objeto JSON con:
1. `resultados`: lista de objetos VMP/VMPP, cada uno con:
   - `vmp`  
   - `vmpp`  
   - `principio_activo`  
   - `dosis`  
   - `forma`  
   - `atc`  
   - `nombre`  
   - `modo_arbol` (estructura jerárquica, si aplica)  
2. `meta`:
   - `datos_obtenidos`: “Datos CIMA (AEMPS) extraídos el DD/MM/AAAA.”  
   - `descargo`: “Esta información no constituye consejo médico; se proporciona solo a efectos informativos.”

**Códigos HTTP**:
- **200 OK**: listado devuelto con éxito.  
- **400 Bad Request**: parámetro inválido (tipo o formato incorrecto).  
- **404 Not Found**: no se encontraron equivalentes clínicos.  
- **502 Bad Gateway**: error en la API externa CIMA.  
- **500 Internal Server Error**: error interno procesando la petición.
"""

maestras_description = """
Devuelve un **listado paginado** de elementos de un catálogo maestro (maestra) según filtros opcionales.

**Uso**:
- Envía los filtros como parámetros de consulta.  
- Si no se especifica ningún filtro, devuelve todos los elementos (paginados).  
- `pagina` indica la página de resultados (entero ≥ 1; por defecto 1).

**Parámetros disponibles** (todos opcionales salvo `maestra`):
- `maestra` (int, requerido): ID de la maestra a consultar:
  - 1: Principios activos  
  - 3: Formas farmacéuticas  
  - 4: Vías de administración  
  - 6: Laboratorios  
  - 7: Códigos ATC  
  - 11: Principios Activos (SNOMED)  
  - 13: Formas farmacéuticas simplificadas (SNOMED)  
  - 14: Vías de administración simplificadas (SNOMED)  
  - 15: Medicamentos  
  - 16: Medicamentos comercializados (SNOMED)  
- `nombre` (str): nombre parcial o exacto del elemento.  
- `id` (str): ID del elemento (solo dígitos).  
- `codigo` (str): código del elemento (ej. ATC).  
- `estupefaciente`, `psicotropo`, `estuopsico`, `enuso` (int; 0 o 1): flags de filtrado.  
- `pagina` (int; ≥ 1): página de resultados (por defecto 1).

**Respuesta** (200 OK):  
JSON con:
1. `resultados`: lista de objetos según la maestra seleccionada.  
2. `meta`:
   - `datos_obtenidos`: “Datos CIMA (AEMPS) extraídos el DD/MM/AAAA.”  
   - `descargo`: “Esta información no constituye consejo médico; se proporciona solo a efectos informativos.”

**Códigos HTTP**:
- **200 OK**: listado devuelto con éxito.  
- **400 Bad Request**: `maestra` ausente o parámetro inválido/rango incorrecto.  
- **404 Not Found**: no hay elementos que cumplan los filtros.  
- **502 Bad Gateway**: error en la API externa CIMA.  
- **500 Internal Server Error**: error interno del servidor.
"""

registro_cambios_description = """
Devuelve el historial de altas, bajas y modificaciones de medicamentos a partir de la fecha indicada y/o para un Nº de registro concreto.

**Uso**:
- Envía los filtros como parámetros de consulta en un GET.  
- `fecha` (opcional): fecha mínima de consulta en formato `dd/mm/yyyy`.  
- `nregistro` (opcional): Número de registro AEMPS (solo dígitos).  
- `metodo` (requerido): método HTTP interno a usar en la llamada (`GET` o `POST`; por defecto `GET`).

**Ejemplo**:
GET /registro-cambios?fecha=01/01/2025&nregistro=12345&metodo=POST

**Respuesta** (200 OK):  
Objeto JSON con:
1. `resultados`: lista de cambios, cada uno con:
   - `nregistro`
   - `tipo_cambio` (“ALTA”, “BAJA” o “MODIFICACION”)
   - `fecha_cambio` (dd/mm/yyyy)
   - `detalle`
2. `meta`:
   - `datos_obtenidos`: “Datos CIMA (AEMPS) extraídos el DD/MM/AAAA.”
   - `descargo`: “Esta información no constituye consejo médico; se proporciona solo a efectos informativos.”

**Códigos HTTP**:
- **200 OK**: petición procesada con éxito.  
- **400 Bad Request**: formato de `fecha` inválido o `metodo` distinto de `GET`/`POST`.  
- **404 Not Found**: no se encontraron cambios para los filtros indicados.  
- **502 Bad Gateway**: error en la API externa CIMA.  
- **500 Internal Server Error**: error interno procesando la petición.
"""

problemas_suministro_description = """
Consulta el estado de suministro de presentaciones farmacéuticas, bien de forma global (todos los problemas activos) o para uno o varios Códigos Nacionales (CN) específicos.

**Uso**:
- Envía los filtros como parámetros de consulta:  
  - `cn` (List[str], opcional): uno o varios Códigos Nacionales (solo dígitos). Repite `cn` para cada valor: `?cn=123&cn=456`.
- Si no se indica `cn`, devuelve el listado global de problemas activos.
- Si se indican uno o varios `cn`, realiza consultas paralelas y agrupa la respuesta por CN.

**Respuesta** (200 OK):
- Objeto JSON con:
  - `data`:
    - **Global** (sin `cn`): objeto con:
      - `totalFilas` (int)  
      - `pagina` (int)  
      - `tamanioPagina` (int)  
      - `resultados` (List[Object]) – cada problema incluye:
        - `cn` (str)  
        - `nombre` (str)  
        - `tipoProblemaSuministro` (int)  
        - `fini` (int, opcional)  
        - `ffin` (int, opcional)  
        - `activo` (bool)  
        - `observ` (str, opcional)
    - **Por CN** (con `cn`): objeto cuyos keys son los CN consultados y values los detalles de ese CN (mismo formato que un elemento de `resultados`).
  - `metadata`:
    - `datos_obtenidos`: “Datos CIMA (AEMPS) extraídos el DD/MM/AAAA.”  
    - `descargo`: “Esta información no constituye consejo médico; se proporciona solo a efectos informativos.”  
    - `tipo_problema_suministros`: diccionario de códigos a descripciones de tipos de problema.

- Si hay errores parciales (al consultar ciertos CN), se añade:
  - `errors`: objeto con cada CN fallido y su detalle de error.

**Códigos HTTP**:
- **200 OK**: petición procesada (total o parcialmente).  
- **404 Not Found**: no se encontraron problemas de suministro para los filtros dados.  
- **502 Bad Gateway**: error upstream consultando la API CIMA.  
- **500 Internal Server Error**: error interno al procesar la petición.
"""

doc_secciones_description = """
Lista los metadatos de secciones disponibles para un tipo de documento y medicamento indicados.

**Uso**:
- Envía los filtros como parámetros de consulta en un GET.  
- Se requiere al menos uno de `nregistro` o `cn`.

**Parámetros**:
- `tipo_doc` (int, path; 1–4, requerido):  
  - 1 = Ficha Técnica  
  - 2 = Prospecto  
  - 3–4 = Otros  
- `nregistro` (str, query, opcional): Número de registro AEMPS (solo dígitos).  
- `cn` (str, query, opcional): Código Nacional (solo dígitos).

**Ejemplo**:  
GET /doc-secciones/1?nregistro=12345

css
Copiar
Editar

**Respuesta** (200 OK):  
{
  "resultados": [
    { "seccion": "4.1", "titulo": "...", "orden": 1 },
    { "seccion": "4.2", "titulo": "...", "orden": 2 },
    …
  ],
  "meta": {
    "datos_obtenidos": "Datos CIMA (AEMPS) extraídos el DD/MM/AAAA.",
    "descargo": "Esta información no constituye consejo médico; se proporciona solo a efectos informativos."
  }
}
Códigos HTTP:
  200 OK: metadatos devueltos con éxito.
  400 Bad Request: faltan nregistro y cn, o tipo_doc fuera de rango (1–4).
  404 Not Found: no se encontraron secciones para los filtros indicados.
  502 Bad Gateway: error en la API externa CIMA.
  500 Internal Server Error: error interno procesando la petición.
"""

doc_contenido_description = """
Devuelve el contenido de secciones de un documento (Ficha Técnica, Prospecto u otros).

Uso:
  Envía los filtros como parámetros de consulta en un GET.
  Se requiere al menos uno de nregistro o cn.
  Si no se indica seccion, devuelve todas las secciones.

Parámetros:
  tipo_doc (int, path; 1–4, requerido):
    1 = Ficha Técnica
    2 = Prospecto
    3–4 = Otros
  nregistro (str, query, opcional): Número de registro AEMPS (solo dígitos).
  cn (str, query, opcional): Código Nacional (solo dígitos).
  seccion (str, query, opcional): ID de sección (p.ej. “4.2”).

Ejemplo:
GET /doc-contenido/2?cn=654321&seccion=5.1
Accept: application/json
Respuesta (200 OK):

Si Accept: application/json:
{
  "seccion": "5.1",
  "titulo": "...",
  "contenido": "<p>…</p>",
  "fecha_actualizacion": "DD/MM/AAAA",
  "meta": {
    "datos_obtenidos": "Datos CIMA (AEMPS) extraídos el DD/MM/AAAA.",
    "descargo": "Esta información no constituye consejo médico; se proporciona solo a efectos informativos."
  }
}
Si Accept: devuelve solo el HTML de la sección.
Si Accept: devuelve solo el texto plano de la sección.

Códigos HTTP:
  200 OK: contenido devuelto con éxito.
  400 Bad Request: faltan nregistro y cn, o tipo_doc fuera de rango (1–4).
  404 Not Found: no se encontró contenido para los parámetros indicados.
  502 Bad Gateway: error en la API externa CIMA.
  500 Internal Server Error: error interno procesando la petición.
"""

listar_notas_description = """
Devuelve las notas de seguridad asociadas a uno o varios medicamentos, identificados por su número de registro AEMPS.

**Uso**:  
- Envía uno o varios `nregistro` como parámetro de consulta:  
  - Para un solo registro: `GET /notas?nregistro=AAA`  
  - Para varios: `GET /notas?nregistro=AAA&nregistro=BBB`

**Parámetro**:  
- `nregistro` (List[str], requerido): uno o varios números de registro (solo dígitos o alfanumérico según CIMA). Repite el parámetro para cada valor.

**Comportamiento**:  
- Si solo hay un `nregistro`, devuelve la lista de notas de seguridad de ese registro.  
- Si hay varios, realiza las llamadas en paralelo y agrupa la respuesta en un objeto:
  {
    "AAA": [ …lista de notas… ],
    "BBB": [ …lista de notas… ]
  }
Respuesta (200 OK):
Objeto JSON con:
resultados (para un solo registro) o el diccionario por registro (varios).
meta:
datos_obtenidos: “Datos CIMA (AEMPS) extraídos el DD/MM/AAAA.”
descargo: “Esta información no constituye consejo médico; se proporciona solo a efectos informativos.”

Códigos HTTP:
200 OK: notas encontradas (total o parcialmente).
400 Bad Request: no se proporcionó al menos un nregistro.
404 Not Found: no se encontraron notas para los registros indicados.
502 Bad Gateway: error upstream al consultar la API CIMA.
500 Internal Server Error: error interno procesando la petición.
"""

obtener_notas_description = """
Devuelve las notas de seguridad para un único medicamento, identificado por su número de registro AEMPS.

Uso:
GET /notas/{nregistro}

Parámetro:
nregistro (str, path; requerido): número de registro AEMPS (solo dígitos o alfanumérico según CIMA).

Respuesta (200 OK):
  Lista de objetos con campos:
    nregistro (str)
    fecha (dd/mm/yyyy)
    titulo (str)
    detalle (str)
    Meta:
    datos_obtenidos: “Datos CIMA (AEMPS) extraídos el DD/MM/AAAA.”
    descargo: “Esta información no constituye consejo médico; se proporciona solo a efectos informativos.”

Códigos HTTP:
  200 OK: notas encontradas con éxito.
  404 Not Found: no se encontraron notas para el registro indicado.
  502 Bad Gateway: error upstream al consultar la API CIMA.
  500 Internal Server Error: error interno procesando la petición.
"""

listar_materiales_description = """
Devuelve los materiales informativos asociados a uno o varios medicamentos, identificados por su número de registro AEMPS.

Uso:
- Envía uno o varios `nregistro` como parámetros de consulta:
  - Para un único registro: `GET /materiales?nregistro=AAA`
  - Para varios registros:  `GET /materiales?nregistro=AAA&nregistro=BBB`

Parámetro:
- `nregistro` (List[str], requerido): uno o varios números de registro (solo dígitos o alfanumérico según CIMA). Repite `nregistro` para cada valor.

Comportamiento:
- Único registro: devuelve la lista de materiales informativos de ese registro.
- Varios registros: realiza llamadas en paralelo y agrupa la respuesta en un objeto:
  ```json
  {
    "AAA": [ /* lista de materiales */ ],
    "BBB": [ /* lista de materiales */ ]
  }
  ```

Respuesta (200 OK):
- Objeto JSON que incluye:
  - `resultados` (para un solo registro) o el diccionario por registro (varios).
  - `meta`:
    - `datos_obtenidos`: "Datos CIMA (AEMPS) extraídos el DD/MM/AAAA."
    - `descargo`:       "Esta información no constituye consejo médico; se proporciona solo a efectos informativos."

Códigos HTTP:
- 200 OK: materiales encontrados (total o parcialmente).
- 400 Bad Request: no se proporcionó al menos un `nregistro`.
- 404 Not Found: no se encontraron materiales para los registros indicados.
- 502 Bad Gateway: error upstream al consultar la API CIMA.
- 500 Internal Server Error: error interno procesando la petición.
"""

obtener_materiales_description = """
Devuelve los materiales informativos asociados a un único medicamento, identificado por su número de registro AEMPS.

Uso:
```
GET /materiales/{nregistro}
```

Parámetro:
- `nregistro` (str, path; requerido): número de registro AEMPS (solo dígitos o alfanumérico según CIMA).

Respuesta (200 OK):
- Lista de objetos con:
  - `nregistro`      (str) — número de registro
  - `tipo_material`  (str) — título o tipo de material
  - `url`            (str) — enlace al documento
- Meta:
  - `datos_obtenidos`: "Datos CIMA (AEMPS) extraídos el DD/MM/AAAA."
  - `descargo`:       "Esta información no constituye consejo médico; se proporciona solo a efectos informativos."

Códigos HTTP:
- 200 OK: materiales encontrados con éxito.
- 404 Not Found: no se encontraron materiales para el registro indicado.
- 502 Bad Gateway: error upstream al consultar la API CIMA.
- 500 Internal Server Error: error interno procesando la petición.
"""

html_ft_multiple_description = """
Descarga o devuelve el HTML completo de la ficha técnica para uno o varios medicamentos.

Uso:
- Para varios registros: `GET /doc-html/ft?nregistro=AAA&nregistro=BBB&filename=FichaTecnica.html`  
- Para uno solo: si solo se incluye un `nregistro`, se devuelve directamente el HTML.

Parámetros:
- `nregistro` (List[str], requerido): uno o varios números de registro AEMPS. Repite este parámetro por cada valor.  
- `filename` (str, requerido): nombre de archivo HTML deseado (p.ej. "FichaTecnica.html").

Comportamiento:
- **Registro único** (`len(nregistro)==1`): devuelve un `StreamingResponse` con `media_type="text/html"` y el contenido HTML.
- **Múltiples registros**: genera en paralelo el HTML de cada ficha y devuelve un archivo ZIP con las páginas, o bien un JSON si se gestiona así.

Respuesta:
- **Único registro**: `StreamingResponse` con el contenido HTML.
- **Múltiples registros**: `StreamingResponse` con un ZIP (status 200).  

Meta:
- `datos_obtenidos`: "Datos CIMA (AEMPS) extraídos el DD/MM/AAAA."  
- `descargo`: "Esta información no constituye consejo médico; se proporciona solo a efectos informativos."

Códigos HTTP:
- **200 OK**: HTML o ZIP generado correctamente.
- **400 Bad Request**: falta `nregistro` o `filename`.
- **404 Not Found**: no existe ficha técnica para algún registro (en múltiple, incluye errores parciales).
- **502 Bad Gateway**: error upstream al descargar la ficha técnica.
- **500 Internal Server Error**: error interno procesando la petición.
"""

html_ft_description = """
Obtiene el HTML completo de la ficha técnica de un único medicamento.

Uso:
```
GET /doc-html/ft/{nregistro}/{filename}
```

Parámetros:
- `nregistro` (str, path; requerido): número de registro AEMPS.
- `filename` (str, path; requerido): nombre de archivo HTML (p.ej. "FichaTecnica.html").

Respuesta (200 OK):
- `StreamingResponse` con `media_type="text/html"` y el contenido HTML.

Meta:
- `datos_obtenidos`: "Datos CIMA (AEMPS) extraídos el DD/MM/AAAA."  
- `descargo`: "Esta información no constituye consejo médico; se proporciona solo a efectos informativos."

Códigos HTTP:
- **200 OK**: HTML generado correctamente.
- **404 Not Found**: no existe la ficha técnica para el registro indicado.
- **502 Bad Gateway**: error upstream al descargar la ficha técnica.
- **500 Internal Server Error**: error interno procesando la petición.
"""

html_p_multiple_description = """
Descarga o devuelve el HTML completo del prospecto para uno o varios medicamentos.

Uso:
- Para varios registros: `GET /doc-html/p?nregistro=AAA&nregistro=BBB&filename=Prospecto.html`  
- Para uno solo: si solo se incluye un `nregistro`, se devuelve directamente el HTML.

Parámetros:
- `nregistro` (List[str], requerido): uno o varios números de registro AEMPS. Repite este parámetro por cada valor.  
- `filename` (str, requerido): nombre de archivo HTML deseado (p.ej. "Prospecto.html" o sección específica).

Comportamiento:
- **Registro único** (`len(nregistro)==1`): devuelve un `StreamingResponse` con `media_type="text/html"` y el contenido HTML.
- **Múltiples registros**: genera en paralelo el HTML de cada prospecto y devuelve un archivo ZIP con las páginas (status 200).

Respuesta:
- **Único registro**: `StreamingResponse` con el contenido HTML.
- **Múltiples registros**: `StreamingResponse` con un ZIP de HTML.

Meta:
- `datos_obtenidos`: "Datos CIMA (AEMPS) extraídos el DD/MM/AAAA."  
- `descargo`: "Esta información no constituye consejo médico; se proporciona solo a efectos informativos."

Códigos HTTP:
- **200 OK**: HTML o ZIP generado correctamente.
- **400 Bad Request**: falta `nregistro` o `filename`.
- **404 Not Found**: no existe prospecto para algún registro (en múltiple, incluye errores parciales).
- **502 Bad Gateway**: error upstream al descargar el prospecto.
- **500 Internal Server Error**: error interno procesando la petición.
"""

html_p_description = """
Obtiene el HTML completo del prospecto para un único medicamento.

Uso:
```
GET /doc-html/p/{nregistro}/{filename}
```

Parámetros:
- `nregistro` (str, path; requerido): número de registro AEMPS.
- `filename` (str, path; requerido): nombre de archivo HTML (p.ej. "Prospecto.html" o sección específica).

Respuesta (200 OK):
- `StreamingResponse` con `media_type="text/html"` y el contenido HTML.

Meta:
- `datos_obtenidos`: "Datos CIMA (AEMPS) extraídos el DD/MM/AAAA."  
- `descargo`: "Esta información no constituye consejo médico; se proporciona solo a efectos informativos."

Códigos HTTP:
- **200 OK**: HTML generado correctamente.
- **404 Not Found**: no existe el prospecto para el registro indicado.
- **502 Bad Gateway**: error upstream al descargar el prospecto.
- **500 Internal Server Error**: error interno procesando la petición.
"""

descargar_ipt_description = """
Descarga los archivos IPT (Informe de Posicionamiento Terapéutico) para uno o varios medicamentos.

Uso:
- Envía los filtros como parámetros de consulta en un GET:  
  - `cn` (List[str], opcional): uno o varios Códigos Nacionales. Repite `cn` por cada valor: `?cn=123&cn=456`.  
  - `nregistro` (List[str], opcional): uno o varios Números de Registro AEMPS. Repite `nregistro` por valor.  
  - `zip` (bool, opcional): `true` para recibir un ZIP con todos los IPT; por defecto JSON de URLs.

Parámetros obligatorios:
- Al menos uno de `cn` o `nregistro`.

Comportamiento:
- **Sin `zip`** (por defecto): devuelve JSON con:
  ```json
  {
    "urls": ["https://.../data/ipt1.pdf", ...],
    "errors": { "cn=999": { "detail": "..." } }  // si hubo errores parciales
  }
  ```
- **Con `zip=true`**: devuelve un `StreamingResponse` con `media_type="application/x-zip-compressed"` y ZIP descargable.

Respuesta (200 OK):
- JSON de URLs o ZIP.

Códigos HTTP:
- **200 OK**: IPTs generados correctamente.
- **400 Bad Request**: faltan `cn` y `nregistro` o parámetros inválidos.
- **404 Not Found**: no se descargó ningún IPT (todos fallaron).
- **502 Bad Gateway**: error upstream al descargar algún IPT.
- **500 Internal Server Error**: error interno procesando la solicitud.
"""

identificar_medicamento_description = """
Identifica hasta 10 presentaciones de medicamentos en el fichero `Presentaciones.xls` usando filtros y paginación.

Uso:
- Envía los filtros como parámetros de consulta en un GET:  
  - `nregistro` (str): coincidencia exacta del Nº Registro AEMPS.  
  - `cn`        (str): coincidencia exacta del Código Nacional.  
  - `nombre`    (str): coincidencia parcial o difusa en el nombre de la presentación.  
  - `laboratorio`   (str): coincidencia parcial en el laboratorio fabricante.  
  - `atc`            (str): coincidencia parcial en el código ATC.  
  - `estado`         (str): coincidencia parcial en el estado.  
  - `comercializado` (bool): `true`/`false` para filtrar por comercializado.  
  - `pagina`    (int, ≥1; opcional): página de resultados (por defecto 1).  
  - `page_size` (int, 1–100; opcional): tamaño de página (por defecto 10).

Parámetros obligatorios:
- Al menos uno de `nregistro`, `cn` o `nombre`.

Comportamiento:
- Aplica los filtros exactos o parciales en el DataFrame cargado.  
- Para `nombre`, si no hay coincidencias directas, usa búsqueda difusa (`difflib.get_close_matches`) hasta 10 resultados.  
- Paginación sobre el conjunto filtrado.

Respuesta** (200 OK):
```json
{
  "data": [ /* lista de hasta page_size registros */ ],
  "metadata": {
    /* parámetros de consulta y total de resultados */
    "datos_obtenidos": "Datos extraídos de Presentaciones.xls el DD/MM/AAAA.",
    "descargo": "Esta información no constituye consejo médico; solo informativa."
  }
}
```

Códigos HTTP:
- **200 OK**: búsqueda y paginación correctas.  
- **400 Bad Request**: no se especificó al menos uno de `nregistro`, `cn` o `nombre`, o parámetros fuera de rango.  
"""

nomenclator_description = """
Realiza búsquedas avanzadas en el Nomenclátor de facturación de productos farmacéuticos.

Uso:
- Envía los filtros como parámetros de consulta en un GET.
- Si no se especifica ningún filtro, devuelve todos los registros (paginados).
- `pagina` indica la página de resultados (entero ≥1; por defecto 1).
- `page_size` indica el número de resultados por página (1–100; por defecto 10).

Parámetros disponibles (todos opcionales salvo que se requiera al menos uno según contexto):
- `codigo_nacional`      (str): coincidencia exacta del Código Nacional.
- `nombre_producto`      (str): coincidencia parcial (case-insensitive) en el nombre del producto.
- `tipo_farmaco`         (str): coincidencia parcial en el tipo de fármaco.
- `principio_activo`     (str): coincidencia parcial en el principio activo o asociación.
- `codigo_laboratorio`   (str): coincidencia exacta del código de laboratorio ofertante.
- `nombre_laboratorio`   (str): coincidencia parcial en el nombre del laboratorio.
- `estado`               (str): coincidencia parcial en el estado (p.ej. "ALTA", "BAJA").
- `fecha_alta_desde`     (str): fecha de alta ≥dd/mm/yyyy.
- `fecha_alta_hasta`     (str): fecha de alta ≤dd/mm/yyyy.
- `fecha_baja_desde`     (str): fecha de baja ≥dd/mm/yyyy.
- `fecha_baja_hasta`     (str): fecha de baja ≤dd/mm/yyyy.
- `aportacion_beneficiario` (str): coincidencia parcial en la aportación del beneficiario.
- `precio_min_iva`       (float): precio venta público mínimo con IVA.
- `precio_max_iva`       (float): precio venta público máximo con IVA.
- `agrupacion_codigo`    (str): coincidencia exacta del código de agrupación homogénea.
- `agrupacion_nombre`    (str): coincidencia parcial en el nombre de agrupación homogénea.
- `diagnostico_hospitalario` (bool): true para sólo diagnóstico hospitalario.
- `larga_duracion`       (bool): true para tratamiento de larga duración.
- `especial_control`     (bool): true para especial control médico.
- `medicamento_huerfano` (bool): true para medicamento huérfano.
- `pagina`               (int): página de resultados (≥1).
- `page_size`            (int): resultados por página (1–100).

Respuesta (200 OK):
- JSON con:
  - `data`: lista de objetos, cada uno con todas las columnas del Nomenclátor correspondientes al filtro.
  - `metadata`:
    - campos de consulta y `total` de registros encontrados.
    - `datos_obtenidos`: "Datos extraídos de Nomenclátor el DD/MM/AAAA."
    - `descargo`: "Información meramente informativa; no sustituye consejo médico."

Códigos HTTP:
- **200 OK**: búsqueda completada (total o parcialmente).
- **400 Bad Request**: formato de fecha inválido o rango de valores fuera de límite.
- **404 Not Found**: no se encontraron registros según los filtros.
- **500 Internal Server Error**: error interno procesando la petición.
"""

system_info_prompt_description = """
Devuelve el `MCP_AEMPS_SYSTEM_PROMPT`, que contiene:
- Descripción completa de las herramientas MCP disponibles.
- Flujo recomendado para el uso de cada una.
- Pautas y descargos de responsabilidad para las respuestas producidas por el agente.

**Uso**:
- Invoca este endpoint para obtener el prompt base que utiliza el agente farmacéutico digital.

**Respuesta**:
Cadena de texto con todo el contenido del prompt.
"""
