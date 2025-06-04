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
Devuelve todos los datos regulatorios disponibles para un medicamento concreto,
identificado por su Código Nacional (`cn`) o por su Número de Registro (`nregistro`).

**Uso**:
- Si se conoce el Código Nacional (CN), pásalo en el parámetro `cn`.
- Si se conoce el Número de Registro AEMPS, pásalo en `nregistro`.
- No es necesario aportar ambos; basta uno de los dos.

**Respuesta**:  
Un objeto JSON que contiene información completa sobre el medicamento e
información meta asociada (descargo, fecha de obtención de información)

**Errores posibles**:
- `400 Bad Request`: Ningún parámetro (`cn` o `nregistro`) ha sido proporcionado.
- `404 Not Found`: No se localiza ningún medicamento con esos datos.
"""

medicamentos_description = """
Devuelve un listado paginado de medicamentos que cumplen las condiciones de búsqueda
introducidas. Se pueden aplicar más de 20 filtros distintos para refinar los resultados.

**Uso**:
- Proporciona uno o varios de los parámetros facultativos para filtrar.  
- Si no se especifica ningún filtro, se listan todos los medicamentos (paginados).  
- El parámetro `pagina` indica el número de página de resultados (mínimo 1).

**Parámetros disponibles**:
- `nombre` (str): Nombre del medicamento (coincidencia parcial o exacta).  
- `laboratorio` (str): Nombre del laboratorio fabricante.  
- `practiv1` (str): Nombre del principio activo principal.  
- `practiv2` (str): Nombre de un segundo principio activo.  
- `idpractiv1` (str): ID numérico del principio activo principal.  
- `idpractiv2` (str): ID numérico de un segundo principio activo.  
- `cn` (str): Código Nacional del medicamento.  
- `atc` (str): Código ATC o descripción parcial del mismo.  
- `nregistro` (str): Número de registro AEMPS.  
- `npactiv` (int): Número de principios activos asociados.  
- `triangulo` (int): 1 = Tienen triángulo, 0 = No tienen triángulo.  
- `huerfano` (int): 1 = Huérfano, 0 = No huérfano.  
- `biosimilar` (int): 1 = Biosimilar, 0 = No biosimilar.  
- `sust` (int):  
  - 1 = Biológicos  
  - 2 = Principios activos de estrecho margen terapéutico  
  - 3 = Medicamentos con control médico o medidas especiales de seguridad  
  - 4 = Medicamentos inhalados para aparato respiratorio  
  - 5 = Medicamentos de estrecho margen terapéutico  
- `vmp` (str): ID del código VMP para equivalentes clínicos.  
- `comerc` (int): 1 = Comercializados, 0 = No comercializados.  
- `autorizados` (int): 1 = Solo medicamentos autorizados, 0 = Solo no autorizados.  
- `receta` (int): 1 = Con receta, 0 = Sin receta.  
- `estupefaciente` (int): 1 = Incluye estupefacientes, 0 = Excluye.  
- `psicotropo` (int): 1 = Incluye psicótropos, 0 = Excluye.  
- `estuopsico` (int): 1 = Incluye estupefacientes o psicótropos, 0 = Excluye.  
- `pagina` (int): Número de página (mínimo 1).  

**Respuesta**:  
Un objeto JSON que contiene:  
1. `resultados`: lista de medicamentos que cumplen los filtros.
2. `meta`: información adicional obligatoria  
   - `datos_obtenidos`: “Datos CIMA (AEMPS) extraídos el DD/MM/AAAA.”  
   - `descargo`: “Esta información no constituye consejo médico; se proporciona solo a efectos informativos.”

**Errores posibles**:
- `400 Bad Request`: Parámetros fuera de rango o tipo inválido.  
- `404 Not Found`: No se encontraron medicamentos para los filtros proporcionados.
"""

buscar_ficha_tecnica_description = """
Permite realizar búsquedas textuales dentro de secciones concretas de la ficha técnica de uno o varios medicamentos.

**Uso**:
- Envía una lista de reglas en formato JSON en el cuerpo de la petición.
- Cada regla debe incluir:
  - `seccion` (string): sección de la ficha técnica donde buscar, en formato “N” o “N.N” (por ejemplo, “4.1”).
  - `texto` (string): palabra o frase a buscar en esa sección.
  - `contiene` (int):  
    - `1` para indicar que la sección debe contener ese texto.  
    - `0` para indicar que la sección no debe contener ese texto.

**Estructura del cuerpo**:
```json
[
  {
    "seccion": "4.1",
    "texto": "cáncer",
    "contiene": 1
  }
]
```
Para combinar condiciones (por ejemplo, incluir “acidez” y excluir “estómago” en la misma sección):

```json
[
  {
    "seccion": "4.1",
    "texto": "acidez",
    "contiene": 1
  },
  {
    "seccion": "4.1",
    "texto": "estómago",
    "contiene": 0
  }
]
```

**Respuesta**:
Un objeto JSON con:

- `resultados`: lista de objetos que contienen el texto indicado.
`meta`: información obligatoria
- `datos_obtenidos`: “Datos CIMA (AEMPS) extraídos el DD/MM/AAAA.”
- `descargo`: “Esta información no constituye consejo médico; se proporciona solo a efectos informativos.”

**Ejemplo de llamada**:
POST /ficha-tecnica/buscar
Content-Type: application/json

[
  {
    "seccion": "4.1",
    "texto": "cáncer",
    "contiene": 1
  }
]

**Errores posibles**:

- 400 Bad Request:
  - El cuerpo no es un array de reglas con los campos `seccion`, `texto` y `contiene`.
  - Alguna regla carece de uno de esos campos o `contiene` no es 0 ni 1.

- 404 Not Found: No se encontró ningún medicamento que cumpla las reglas indicadas.
"""

presentaciones_description = """
Devuelve un listado paginado de presentaciones de medicamentos según los filtros indicados.

**Uso**:
- Puedes filtrar por uno o varios de los parámetros disponibles.
- El parámetro `pagina` indica el número de página (mínimo 1).

**Parámetros disponibles**:
- `cn` (str): Código Nacional del medicamento.
- `nregistro` (str): Número de registro AEMPS.
- `vmp` (str): ID del código VMP para equivalentes clínicos.
- `vmpp` (str): ID del código VMPP.
- `idpractiv1` (str): ID del principio activo.
- `comerc` (int): 1 = Comercializados, 0 = No comercializados.
- `estupefaciente` (int): 1 = Incluye estupefacientes, 0 = Excluye.
- `psicotropo` (int): 1 = Incluye psicótropos, 0 = Excluye.
- `estuopsico` (int): 1 = Incluye estupefacientes o psicótropos, 0 = Excluye.
- `pagina` (int): Número de página de resultados (mínimo 1).

**Respuesta**:
Un objeto JSON que contiene:
1. `resultados`: lista de presentaciones que cumplen los filtros.
2. `meta`: información adicional obligatoria
   - `datos_obtenidos`: "Datos CIMA (AEMPS) extraídos el DD/MM/AAAA."
   - `descargo`: "Esta información no constituye consejo médico; se proporciona solo a efectos informativos."

**Errores posibles**:
- `400 Bad Request`: Parámetros fuera de rango o tipo inválido.
- `404 Not Found`: No se encontraron presentaciones para los filtros proporcionados.
"""

presentacion_description = """
Obtiene detalles de presentación para uno o varios medicamentos identificados por su Código Nacional (CN).

**Uso**:
- Para un único CN, pasa `?cn=123456789`.
- Para múltiples CN, repite el parámetro: `?cn=123&cn=456&cn=789`.

**Parámetro**:
- `cn` (List[str]): Lista de Códigos Nacionales. Se repite el parámetro en la URL para cada CN.

**Respuesta**:
- Si solo hay un CN, devuelve directamente el objeto con todos los detalles de esa presentación:
  - `cn` (Código Nacional)
  - `nregistro` (Número de registro AEMPS)
  - `forma` (forma farmacéutica)
  - `dosis` (dosificación)
  - `laboratorio` (nombre del laboratorio)

- Si hay varios CN, devuelve un diccionario con la forma:
  ```json
  {
    "123456789": { ... detalle ... },
    "987654321": { ... detalle ... }
  }
  ```

En ambos casos, se incluye la clave `meta` con:
- `datos_obtenidos`: "Datos CIMA (AEMPS) extraídos el DD/MM/AAAA."
- `descargo`: "Esta información no constituye consejo médico; se proporciona solo a efectos informativos."

**Errores posibles**:
- `400 Bad Request`: No se proporcionó ningún parámetro `cn`.
- `502 Bad Gateway`: Error upstream obteniendo presentación.
- `500 Internal Server Error`: Error interno procesando presentación.
"""

vmpp_description = """
Devuelve una lista de equivalentes clínicos (VMP/VMPP) según los filtros proporcionados.

**Parámetros disponibles**:
- `practiv1` (str): Nombre del principio activo principal.
- `idpractiv1` (str): ID del principio activo principal.
- `dosis` (str): Dosis del medicamento.
- `forma` (str): Nombre de la forma farmacéutica.
- `atc` (str): Código ATC o descripción parcial.
- `nombre` (str): Nombre del medicamento.
- `modoArbol` (int): Si se incluye (cualquier valor), devuelve resultados en modo jerárquico.

**Respuesta**:
Un objeto JSON que contiene:
1. `resultados`: lista de VMP/VMPP que cumplen los filtros. Cada elemento incluye:
   - `vmp` (ID de VMP)
   - `vmpp` (ID de VMPP)
   - `principio_activo` (nombre del PA)
   - `dosis` (dosificación)
   - `forma` (forma farmacéutica)
   - `atc` (código ATC)
   - `nombre` (nombre del medicamento)
   - `modo_arbol` (estructura jerárquica, si aplica)
2. `meta`: información adicional obligatoria
   - `datos_obtenidos`: "Datos CIMA (AEMPS) extraídos el DD/MM/AAAA."
   - `descargo`: "Esta información no constituye consejo médico; se proporciona solo a efectos informativos."

**Errores posibles**:
- `400 Bad Request`: Parámetro inválido.
- `404 Not Found`: No se encontraron equivalentes clínicos para los filtros dados.
"""

maestras_description = """
Devuelve una lista de elementos de un catálogo específico (maestra) según los filtros.

**Parámetros disponibles**:
- `maestra` (int): ID de la maestra a consultar:
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
- `nombre` (str): Nombre del elemento a recuperar.
- `id` (str): ID del elemento a recuperar.
- `codigo` (str): Código del elemento a recuperar.
- `estupefaciente` (int): 1 = Devuelve sólo principios activos estupefacientes.
- `psicotropo` (int): 1 = Devuelve sólo principios activos psicótropos.
- `estuopsico` (int): 1 = Devuelve estupefacientes o psicótropos.
- `enuso` (int): 0 = Devuelve tanto los PA asociados a medicamentos como los no asociados.
- `pagina` (int): Número de página (si la API lo soporta).

**Respuesta**:
Un objeto JSON que contiene:
1. `resultados`: lista de elementos que cumplen los filtros. Cada elemento incluye campos según la maestra seleccionada:
   - Para ATC: `id`, `codigo`, `descripcion`, `fecha_actualizacion`, etc.
   - Para Principios Activos: `id`, `nombre`, `estupefaciente`, `psicotropo`, etc.
   - Para Formas: `id`, `descripcion`, etc.
   - Para Laboratorios: `id`, `nombre`, etc.
2. `meta`: información adicional obligatoria
   - `datos_obtenidos`: "Datos CIMA (AEMPS) extraídos el DD/MM/AAAA."
   - `descargo`: "Esta información no constituye consejo médico; se proporciona solo a efectos informativos."

**Errores posibles**:
- `400 Bad Request`: Parámetros inválidos o rango incorrecto.
- `404 Not Found`: No se encontraron elementos para los filtros dados.
"""

registro_cambios_description = """
Devuelve el historial de altas, bajas y modificaciones para medicamentos desde la fecha indicada.

**Parámetros disponibles**:
- `fecha` (str): Fecha a partir de la cual se desea consultar cambios, en formato "dd/mm/yyyy".
- `nregistro` (str): Número de registro AEMPS. Para múltiples, repetir el parámetro.
- `metodo` (str): "GET" o "POST"; método HTTP que se usará en la llamada interna.

**Respuesta**:
Un objeto JSON que contiene:
1. `resultados`: lista de objetos con:
   - `nregistro` (Número de registro AEMPS)
   - `tipo_cambio` ("ALTA", "BAJA", "MODIFICACION")
   - `fecha_cambio` (fecha en que ocurrió el cambio)
   - `detalle` (descripción de la modificación)
2. `meta`: información adicional obligatoria
   - `datos_obtenidos`: "Datos CIMA (AEMPS) extraídos el DD/MM/AAAA."
   - `descargo`: "Esta información no constituye consejo médico; se proporciona solo a efectos informativos."

**Errores posibles**:
- `400 Bad Request`: Formato de fecha incorrecto (debe ser dd/mm/yyyy) o `metodo` distinto de "GET"/"POST".
- `404 Not Found`: No se encontraron cambios para los parámetros dados.
"""

problemas_suministro_description = """
Permite consultar el estado de suministro de presentaciones farmacéuticas, ya sea de forma global
(paginado de todos los problemas activos) o para uno o varios Códigos Nacionales (CN) específicos.

Parámetros disponibles:
- cn (List[str], opcional): Lista de Códigos Nacionales. Repetir el parámetro para cada CN,
  por ejemplo: ?cn=654321&cn=789012

Comportamiento:
- Si cn no se especifica (valor None), el endpoint devuelve el listado global de problemas de suministro activos,
  tal como ofrece la API CIMA en /psuministro?pagina={num}&pagesize={num}. Retorna un objeto JSON con campos:
  totalFilas (int): Total de registros
  pagina (int): Página actual
  tamanioPagina (int): Tamaño de página
  resultados (List[Object]): Lista de problemas donde cada objeto incluye:
    cn (str): Código Nacional
    nombre (str): Nombre del medicamento
    tipoProblemaSuministro (int): Tipo de problema (véase tabla meta en meta)
    fini (int, opcional): Fecha de inicio (timestamp en milisegundos)
    ffin (int, opcional): Fecha de fin (timestamp en milisegundos)
    activo (bool): Indica si el problema está activo
    observ (str, opcional): Observaciones

- Si se proporciona uno o varios valores en cn, se realizan llamadas en paralelo a psuministro(cn)
  para cada código, devolviendo un diccionario con la forma { "654321": {...}, "789012": {...} }
  donde cada valor es el objeto JSON con los detalles del problema de suministro para ese CN.

En ambos casos, se añade en la capa MCP una sección meta con:
{
  "datos_obtenidos": "Datos CIMA (AEMPS) extraídos el DD/MM/AAAA.",
  "descargo": "Esta información no constituye consejo médico; se proporciona solo a efectos informativos.",
  "tipo_problema_suministros": Diccionario con ids y tipo de problema de suministro para comparar con la respuesta de la consulta.
}

Errores posibles:
- 502 Bad Gateway: Error upstream al consultar la API CIMA.
- 500 Internal Server Error: Error interno en el servidor al procesar la solicitud.
"""

doc_secciones_description = """
Lista los metadatos (sección, título y orden) de las secciones existentes para el tipo de documento y medicamento indicado.
Se requiere al menos `nregistro` o `cn`.

**Parámetros disponibles**:
- `tipo_doc` (int): Tipo de documento:
  - 1 = Ficha Técnica
  - 2 = Prospecto
  - 3-4 = Otros
- `nregistro` (str): Número de registro del medicamento.
- `cn` (str): Código Nacional del medicamento.

**Respuesta**:
Un objeto JSON con:
1. `resultados`: lista de objetos con metadatos de sección:
   - `seccion` (ID de sección, p.ej. "4.1")
   - `titulo` (título de la sección)
   - `orden` (orden secuencial)
2. `meta`: información obligatoria:
   - `datos_obtenidos`: "Datos CIMA (AEMPS) extraídos el DD/MM/AAAA."
   - `descargo`: "Esta información no constituye consejo médico; se proporciona solo a efectos informativos."

**Errores posibles**:
- `400 Bad Request`: No se proporcionó `nregistro` o `cn`, o `tipo_doc` fuera de rango (1-4).
- `404 Not Found`: No se encontraron metadatos para los parámetros dados.
"""

doc_contenido_description = """
Devuelve el contenido (en HTML o JSON) de las secciones de un documento para el tipo y medicamento indicados.
Si se especifica `seccion`, retorna solo esa sección.

**Parámetros disponibles**:
- `tipo_doc` (int): Tipo de documento:
  - 1 = Ficha Técnica
  - 2 = Prospecto
  - 3-4 = Otros
- `nregistro` (str): Número de registro del medicamento.
- `cn` (str): Código Nacional del medicamento.
- `seccion` (str): ID de la sección a obtener, p.ej. "4.2". Si no se indica, se devuelven todas.

**Respuesta**:
Dependerá del encabezado "Accept":
- `application/json`: JSON con campos:
  - `seccion` (ID de sección)
  - `titulo` (título)
  - `contenido` (HTML o texto según formato)
  - `fecha_actualizacion` (fecha de obtención)
- `text/html`: Solo el HTML de la sección (sin cabeceras ni menú lateral).
- `text/plain`: Solo el texto plano de la sección.

**Errores posibles**:
- `400 Bad Request`: No se proporcionó `nregistro` ni `cn`, o `tipo_doc` fuera de rango (1-4).
- `404 Not Found`: No se encontró contenido para los parámetros dados.
"""

listar_notas_description = """
Devuelve las notas de seguridad asociadas a uno o varios medicamentos identificados por su número de registro.

**Parámetro disponible**:
- `nregistro` (List[str]): Uno o varios números de registro AEMPS. Repetir el parámetro para cada valor, por ejemplo: `?nregistro=AAA&nregistro=BBB`.

**Comportamiento**:
- Si solo se proporciona un `nregistro`, devuelve la lista de notas de seguridad para ese registro.
- Si se proporcionan varios `nregistro`, se generan llamadas en paralelo y se devuelve un objeto JSON con la forma `{ "AAA": [...], "BBB": [...] }`.

**Respuesta**:
- Para un solo registro: lista de objetos con campos como:
  - `nregistro` (Número de registro)
  - `fecha` (fecha de la nota)
  - `titulo` (título de la nota)
  - `detalle` (descripción detallada)
- Para varios registros: diccionario con claves por `nregistro` y valores las listas de notas correspondientes.
  En ambos casos, se incluye `meta` con:
  - `datos_obtenidos`: "Datos CIMA (AEMPS) extraídos el DD/MM/AAAA."
  - `descargo`: "Esta información no constituye consejo médico; se proporciona solo a efectos informativos."

**Errores posibles**:
- `400 Bad Request`: No se proporcionó al menos un `nregistro`.
- `502 Bad Gateway`: Error upstream al listar notas.
- `500 Internal Server Error`: Error interno al procesar notas.
"""

obtener_notas_description = """
Devuelve las notas de seguridad para un único medicamento, identificado por su número de registro.

**Parámetro**:
- `nregistro` (str): Número de registro AEMPS.

**Respuesta**:
Lista de objetos con campos:
- `nregistro` (Número de registro)
- `fecha` (fecha de la nota)
- `titulo` (título de la nota)
- `detalle` (descripción detallada)

Se incluye `meta` con:
- `datos_obtenidos`: "Datos CIMA (AEMPS) extraídos el DD/MM/AAAA."
- `descargo`: "Esta información no constituye consejo médico; se proporciona solo a efectos informativos."

**Errores posibles**:
- `404 Not Found`: No se encontraron notas para el registro dado.
"""

listar_materiales_description = """
Devuelve los materiales informativos asociados a uno o varios medicamentos identificados por su número de registro.

**Parámetro disponible**:
- `nregistro` (List[str]): Uno o varios números de registro AEMPS. Repetir el parámetro para cada valor.

**Comportamiento**:
- Si solo se proporciona un `nregistro`, devuelve la lista de materiales informativos para ese registro.
- Si se proporcionan varios `nregistro`, se generan llamadas en paralelo y se devuelve un objeto JSON con `{ "AAA": [...], "BBB": [...] }`.

**Respuesta**:
- Para un solo registro: lista de objetos con campos:
  - `nregistro` (Número de registro)
  - `tipo_material` (título o tipo de material)
  - `url` (enlace al documento)
- Para varios registros: diccionario con claves por `nregistro` y valores las listas de materiales correspondientes.
  En ambos casos, se incluye `meta` con:
  - `datos_obtenidos`: "Datos CIMA (AEMPS) extraídos el DD/MM/AAAA."
  - `descargo`: "Esta información no constituye consejo médico; se proporciona solo a efectos informativos."

**Errores posibles**:
- `400 Bad Request`: No se proporcionó al menos un `nregistro`.
- `502 Bad Gateway`: Error upstream al listar materiales.
- `500 Internal Server Error`: Error interno al procesar materiales.
"""

obtener_materiales_description = """
Devuelve los materiales informativos asociados a un único medicamento, identificado por su número de registro.

**Parámetro**:
- `nregistro` (str): Número de registro AEMPS.

**Respuesta**:
Lista de objetos con campos:
- `nregistro` (Número de registro)
- `tipo_material` (título o tipo de material)
- `url` (enlace al documento)

Se incluye `meta` con:
- `datos_obtenidos`: "Datos CIMA (AEMPS) extraídos el DD/MM/AAAA."
- `descargo`: "Esta información no constituye consejo médico; se proporciona solo a efectos informativos."

**Errores posibles**:
- `404 Not Found`: No se encontraron materiales para el registro dado.
"""

html_ft_multiple_description = """
Obtiene el HTML completo de la ficha técnica para uno o varios medicamentos.

**Parámetros disponibles**:
- `nregistro` (List[str]): Uno o varios números de registro AEMPS. Repetir el parámetro para cada valor, por ejemplo: `?nregistro=AAA&nregistro=BBB`.
- `filename` (str): Nombre de archivo HTML que se desea (p.ej. 'FichaTecnica.html').

**Comportamiento**:
- Si solo se proporciona un `nregistro`, devuelve un `StreamingResponse` con el contenido HTML directamente.
- Si se proporcionan varios `nregistro`, descarga en paralelo el HTML de cada uno y devuelve un objeto JSON con la forma `{ "AAA": "<html>…</html>", "BBB": "<html>…</html>" }`.

**Respuesta**:
- Para un solo registro: `StreamingResponse` con `media_type="text/html"`.
- Para múltiples registros: JSON con claves por `nregistro` y valores con el HTML en cadena.
  Además, se incluye en el objeto `meta`:
  - `datos_obtenidos`: "Datos CIMA (AEMPS) extraídos el DD/MM/AAAA."
  - `descargo`: "Esta información no constituye consejo médico; se proporciona solo a efectos informativos."

**Errores posibles**:
- `400 Bad Request`: No se proporcionó al menos un `nregistro` o `filename`.
- `502 Bad Gateway`: Error upstream al descargar la ficha técnica.
- `500 Internal Server Error`: Error interno al procesar la ficha técnica.
"""

html_ft_description = """
Obtiene el HTML completo de la ficha técnica para un único medicamento.

**Parámetros**:
- `nregistro` (str): Número de registro AEMPS.
- `filename` (str): Nombre de archivo HTML deseado ('FichaTecnica.html').

**Respuesta**:
`StreamingResponse` con el contenido HTML y `media_type="text/html"`.

**Errores posibles**:
- `404 Not Found`: No existe la ficha técnica para el registro proporcionado.
- `500 Internal Server Error`: Error interno al procesar la ficha técnica.
"""

html_p_multiple_description = """
Obtiene el HTML completo del prospecto para uno o varios medicamentos.

**Parámetros disponibles**:
- `nregistro` (List[str]): Uno o varios números de registro AEMPS. Repetir el parámetro para cada valor.
- `filename` (str): Nombre de archivo HTML que se desea (p.ej. 'Prospecto.html' o sección específica).

**Comportamiento**:
- Si solo se proporciona un `nregistro`, devuelve un `StreamingResponse` con el contenido HTML.
- Si se proporcionan varios `nregistro`, descarga en paralelo el HTML de cada uno y devuelve un objeto JSON con `{ "NR1": "<html>…</html>", "NR2": "<html>…</html>" }`.

**Respuesta**:
- Para un solo registro: `StreamingResponse` con `media_type="text/html"`.
- Para múltiples registros: JSON con claves por `nregistro` y valores con el HTML en cadena.
  Se incluye `meta` con:
  - `datos_obtenidos`: "Datos CIMA (AEMPS) extraídos el DD/MM/AAAA."
  - `descargo`: "Esta información no constituye consejo médico; se proporciona solo a efectos informativos."

**Errores posibles**:
- `400 Bad Request`: Falta `nregistro` o `filename`.
- `502 Bad Gateway`: Error upstream al descargar el prospecto.
- `500 Internal Server Error`: Error interno al procesar el prospecto.
"""

html_p_description = """
Obtiene el HTML completo del prospecto para un único medicamento.

**Parámetros**:
- `nregistro` (str): Número de registro AEMPS.
- `filename` (str): Nombre de archivo HTML deseado ('Prospecto.html' o sección específica).

**Respuesta**:
`StreamingResponse` con `media_type="text/html"`.

**Errores posibles**:
- `404 Not Found`: No existe el prospecto para el registro dado.
- `500 Internal Server Error`: Error interno al procesar el prospecto.
"""

descargar_ipt = """
Descarga los archivos IPT (Informe de Posicionamiento Terapéutico) asociados a uno o más medicamentos,
identificados por su Código Nacional (`cn`) y/o Número de Registro (`nregistro`).

**Parámetros disponibles**:
- `cn` (List[str], opcional): Lista de Códigos Nacionales. Repetir el parámetro para cada CN, por ejemplo: `?cn=123&cn=456`.
- `nregistro` (List[str], opcional): Lista de Números de Registro AEMPS. Repetir el parámetro para cada valor.

**Comportamiento**:
- Es obligatorio proporcionar al menos un `cn` o un `nregistro`.
- Si se especifica un solo valor, retorna una lista de rutas de archivos IPT para ese único medicamento.
- Si se especifican varios valores, descarga en paralelo todos los documentos IPT y concatena las rutas en una única lista.

**Respuesta**:
Lista de cadenas (`List[str]`), donde cada elemento es la ruta en servidor al archivo IPT descargado.

**Errores posibles**:
- `400 Bad Request`: No se proporcionó ni `cn` ni `nregistro`.
- `502 Bad Gateway`: Error upstream al intentar descargar alguno de los IPT.
- `500 Internal Server Error`: Error interno al procesar la solicitud.
"""

identificar_medicamento = """
Busca hasta 10 presentaciones de medicamentos en el archivo `Presentaciones.xls` según:
- `nregistro`: coincidencia exacta del número de registro.
- `cn`: coincidencia exacta del Código Nacional.
- `nombre`: coincidencia parcial o búsqueda difusa en el nombre de la presentación.

**Parámetros (al menos uno es obligatorio)**:
- `nregistro` (str, opcional): Número de registro AEMPS.
- `cn` (str, opcional): Código Nacional del medicamento.
- `nombre` (str, opcional): Nombre (o parte) de la presentación. Si no hay coincidencias exactas,
  se realiza una búsqueda difusa para devolver hasta 10 opciones similares.

**Comportamiento**:
- Si se proporciona `nregistro`, filtra las filas cuyo campo "Nº Registro" coincide exactamente.
- Si se proporciona `cn`, filtra por "Cod. Nacional".
- Si se proporciona `nombre`, normaliza texto (quita tildes y mayúsculas), y busca coincidencias parciales.
  Si no se encuentra ninguna, aplica algoritmo de similitud (difflib.get_close_matches) para
  devolver hasta 10 resultados.

**Respuesta**:
Lista de diccionarios (como máximo 10), donde cada diccionario contiene los campos originales
de la hoja de Excel (por ejemplo, "Nº Registro", "Cod. Nacional", "Presentación", etc.).
Si no se encuentra ninguna coincidencia, retorna lista vacía.

**Errores posibles**:
- `400 Bad Request`: No se especificó `nregistro`, `cn` ni `nombre`.
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
