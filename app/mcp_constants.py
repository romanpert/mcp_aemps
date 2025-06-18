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
identificado por su **Código Nacional** (`cn`) o por su **Número de Registro AEMPS** (`nregistro`).

**Uso**  
- Proporciona **solo dígitos** en `cn` para buscar por Código Nacional.  
- Proporciona **solo dígitos** en `nregistro` para buscar por Número de Registro AEMPS.  
- No es necesario enviar ambos parámetros; **será suficiente** con uno de ellos.

**Parámetros**  
- `cn` (opcional, string): Código Nacional del medicamento (solo dígitos).  
- `nregistro` (opcional, string): Número de registro AEMPS (solo dígitos).
"""

medicamentos_description = """
Devuelve un **listado paginado** de medicamentos que cumplen los filtros especificados.

**Uso**  
- Proporciona uno o varios parámetros para refinar la búsqueda.  
- Si no se especifica ningún filtro, se listan **todos** los medicamentos (paginados).  
- El parámetro `pagina` indica la página de resultados (entero ≥ 1; por defecto 1).

**Parámetros disponibles** (todos opcionales)  
- `nombre` (str): coincidencia parcial o exacta del nombre.  
- `laboratorio` (str): nombre del laboratorio fabricante.  
- `practiv1`, `practiv2` (str): nombre del principio activo principal o secundario.  
- `idpractiv1`, `idpractiv2` (str): ID numérico del principio activo (solo dígitos).  
- `cn` (str): Código Nacional (solo dígitos).  
- `atc` (str): código ATC completo o parcial.  
- `nregistro` (str): Número de Registro AEMPS (solo dígitos).  
- `npactiv` (int): número de principios activos asociados.  
- `triangulo`, `huerfano`, `biosimilar`, `comerc`, `autorizados`, `receta`, `estupefaciente`, `psicotropo`, `estuopsico` (int; 0 o 1): flags específicos (1 = incluye, 0 = excluye).  
- `sust` (int; 1–5): tipo especial de medicamento.  
- `vmp` (str): ID de código VMP para equivalentes clínicos.  
- `pagina` (int; ≥ 1): número de página de resultados.
"""

buscar_ficha_tecnica_description = """
Realiza búsquedas textuales dentro de secciones específicas de la ficha técnica de uno o varios medicamentos.

**Uso**  
- Envía en el cuerpo un array JSON de reglas de búsqueda.  
- Cada regla debe incluir:  
  - `seccion` (str): sección de la ficha técnica en formato “N” o “N.N” (p. ej. “4” o “4.1”).  
  - `texto` (str): palabra o frase a buscar.  
  - `contiene` (int): 1 = debe contener ese texto; 0 = no debe contenerlo.

**Ejemplo de cuerpo**  
```json
[
  { "seccion": "4.1", "texto": "cáncer",   "contiene": 1 },
  { "seccion": "4.1", "texto": "estómago", "contiene": 0 }
]
```
"""

presentaciones_description = """
Devuelve un **listado paginado** de presentaciones de medicamentos según filtros opcionales.

**Uso**  
- Envía los filtros como parámetros de consulta.  
- Si no se especifica ningún filtro, se listan **todas** las presentaciones (paginadas).  
- `pagina` indica la página de resultados (entero ≥ 1; por defecto 1).

**Parámetros disponibles** (todos opcionales)  
- `cn` (str): Código Nacional (solo dígitos).  
- `nregistro` (str): Número de registro AEMPS (solo dígitos).  
- `vmp` (str): ID del código VMP para equivalentes clínicos.  
- `vmpp` (str): ID del código VMPP.  
- `idpractiv1` (str): ID numérico del principio activo (solo dígitos).  
- `comerc` (int; 0 o 1): 1 = comercializado, 0 = no comercializado.  
- `estupefaciente`, `psicotropo`, `estuopsico` (int; 0 o 1): flags de inclusión/exclusión (1 = incluye, 0 = excluye).  
- `pagina` (int; ≥ 1): página de resultados (por defecto 1).
"""


presentacion_description = """
Obtiene los detalles de presentación para uno o varios medicamentos identificados por su **Código Nacional** (CN).

**Uso**  
- Envía uno o varios parámetros `cn`:  
  - Único CN: `GET /presentacion?cn=123456789`  
  - Varios CN: `GET /presentacion?cn=123&cn=456&cn=789`

**Parámetro**  
- `cn` (List[str], **requerido**): uno o varios Códigos Nacionales (solo dígitos). Repetir `cn` por cada valor.
"""


vmpp_description = """
Devuelve un **listado paginado** de equivalentes clínicos VMP/VMPP según filtros opcionales.

**Uso**  
- Envía los filtros como parámetros de consulta.  
- Si no se especifica ningún filtro, se listan **todos** los registros (paginados).  
- `pagina` indica la página de resultados (entero ≥ 1; por defecto 1).

**Parámetros disponibles** (todos opcionales)  
- `practiv1` (str): nombre del principio activo principal.  
- `idpractiv1` (str): ID numérico del principio activo (solo dígitos).  
- `dosis` (str): dosis del medicamento (según CIMA).  
- `forma` (str): forma farmacéutica.  
- `atc` (str): código ATC completo o parcial.  
- `nombre` (str): nombre del medicamento.  
- `modoArbol` (int; 0 o 1): 1 = respuesta en modo jerárquico, 0 = plano.  
- `pagina` (int; ≥ 1): número de página de resultados.
"""

maestras_description = """
Devuelve un **listado paginado** de elementos de un catálogo maestro (maestra) según filtros opcionales.

**Uso**  
- Envía los filtros como parámetros de consulta.  
- Si no se especifica ningún filtro, se listan **todos** los elementos (paginados).  
- `pagina` indica la página de resultados (entero ≥ 1; por defecto 1).

**Parámetros disponibles** (todos opcionales salvo `maestra`)  
- `maestra` (int, **requerido**): ID de la maestra a consultar:  
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
- `pagina` (int; ≥ 1): número de página de resultados.
"""

registro_cambios_description = """
Devuelve el historial de altas, bajas y modificaciones de medicamentos a partir de la fecha indicada y/o para un Nº de registro concreto.

**Uso**  
- Envía los filtros como parámetros de consulta en un GET.  
- `fecha` (opcional): fecha mínima de consulta en formato `dd/mm/yyyy`.  
- `nregistro` (opcional): Número de registro AEMPS (solo dígitos).  
- `metodo` (requerido): método HTTP interno a usar (`GET` o `POST`; por defecto `GET`).

**Ejemplo**  
GET /registro-cambios?fecha=01/01/2025&nregistro=12345&metodo=POST
"""

problemas_suministro_description = """
Consulta el estado de suministro de presentaciones farmacéuticas, de forma global (con paginación) o para uno o varios Códigos Nacionales (CN).
Priorizable usarlo por Código Nacional (cn).

**Uso**  
- **Global** (sin `cn`):  
  `GET /problemas-suministro[?pagina={n}&tamanioPagina={m}]`  
  - `pagina` y `tamanioPagina` controlan la paginación; sólo se aplican cuando no hay `cn`.
- **Por CN**:  
  `GET /problemas-suministro?cn=654987&cn=712345`

**Parámetros**  
- `cn` (List[str], opcional): uno o varios Códigos Nacionales (solo dígitos). Repite `cn` por cada valor.  
- `pagina` (int, opcional, defecto=1): número de página de resultados (sólo sin `cn`). Valor mínimo 1.  
- `tamanioPagina` (int, opcional, defecto=10): número de elementos por página (sólo sin `cn`). Rango 1–100.  
"""

doc_secciones_description = """
Lista los metadatos de secciones disponibles para un tipo de documento y medicamento indicados.

**Uso**  
- Envía los filtros como parámetros de consulta en un GET.  
- Se requiere al menos uno de `nregistro` o `cn`.

**Parámetros**  
- `tipo_doc` (int, path; 1–4, **requerido**):
  - 1 = Ficha Técnica
  - 2 = Prospecto
  - 3–4 = Otros
- `nregistro` (str, query, opcional): Número de registro AEMPS (solo dígitos).
- `cn` (str, query, opcional): Código Nacional (solo dígitos).

**Ejemplo**  
```
GET /doc-secciones/1?nregistro=12345
```
"""

doc_contenido_description = """
Devuelve el contenido de secciones de un documento (Ficha Técnica, Prospecto u otros).

**Uso**  
- Envía los filtros como parámetros de consulta en un GET.  
- Se requiere al menos uno de `nregistro` o `cn`.
- Si no se indica `seccion`, devuelve todas las secciones.

**Parámetros**  
- `tipo_doc` (int, path; 1–2, **requerido**):
  - 1 = Ficha Técnica
  - 2 = Prospecto
- `nregistro` (str, query, opcional): Número de registro AEMPS (solo dígitos).
- `cn` (str, query, opcional): Código Nacional (solo dígitos).
- `seccion` (str, query, opcional): ID de sección (p. ej. "4.2").
- `format` (str, query, opcional): formato de respuesta: `json` (por defecto), `html` o `txt`.

**Ejemplo**  
```
GET /doc-contenido/2?cn=654321&seccion=5.1
Accept: application/json
```
"""


listar_notas_description = """
Devuelve las notas de seguridad asociadas a uno o varios medicamentos, identificados por su número de registro AEMPS.

**Uso**  
- Envía uno o varios parámetros `nregistro` en la consulta:  
  - Un único registro: `GET /notas?nregistro=AAA`  
  - Varios registros: `GET /notas?nregistro=AAA&nregistro=BBB`

**Parámetro**  
- `nregistro` (List[str], **requerido**): uno o varios números de registro (dígitos o alfanuméricos según CIMA). Repite el parámetro por cada valor.

**Comportamiento**  
- **Único registro**: devuelve la lista de notas de seguridad para ese registro.
- **Múltiples registros**: realiza llamadas concurrentes y agrupa la respuesta en un objeto:
  ```json
  {
    "AAA": [ …lista de notas… ],
    "BBB": [ …lista de notas… ]
  }
  ```
"""

obtener_notas_description = """
Devuelve las notas de seguridad para uno o varios medicamentos, identificados por su número de registro AEMPS.

**Uso**  
- Envía el parámetro `nregistro` en la ruta:
  - Múltiples registros separados por comas: `GET /notas/AAA,BBB,CCC`

**Parámetro**  
- `nregistro` (str, **requerido**): uno o varios números de registro separados por comas (solo dígitos o alfanuméricos según CIMA).

**Comportamiento**  
- Divide la lista y llama individualmente a la API para cada registro.
- Agrupa los resultados en un objeto y registra errores parciales.
"""

listar_materiales_description = """
Devuelve los materiales informativos asociados a uno o varios medicamentos, identificados por su número de registro AEMPS.

**Uso**  
- Envía uno o varios parámetros `nregistro` como consulta:
  - Un solo registro: `GET /materiales?nregistro=AAA`
  - Varios registros: `GET /materiales?nregistro=AAA&nregistro=BBB`

**Parámetro**  
- `nregistro` (List[str], **requerido**): uno o varios números de registro (dígitos o alfanuméricos según CIMA). Repite el parámetro por cada valor.

**Comportamiento**  
- **Único registro**: devuelve la lista de materiales para ese registro.
- **Múltiples registros**: llamadas concurrentes y agrupa la respuesta en un objeto:
  ```json
  {
    "AAA": [ /* lista de materiales */ ],
    "BBB": [ /* lista de materiales */ ]
  }
  ```
"""

obtener_materiales_description = """
Devuelve los materiales informativos asociados a un único medicamento, identificado por su número de registro AEMPS.

**Uso**  
```
GET /materiales/{nregistro}
```

**Parámetro**  
- `nregistro` (str, **requerido**): número de registro AEMPS (dígitos o alfanuméricos según CIMA).
"""

html_ft_multiple_description = """
Descarga o devuelve el HTML completo de la ficha técnica para uno o varios medicamentos.

**Uso**  
- Múltiples registros: `GET /doc-html/ft?nregistro=AAA&nregistro=BBB&filename=FichaTecnica.html`  
- Único registro: si solo hay un `nregistro`, devuelve directamente el HTML.

**Parámetros**  
- `nregistro` (List[str], **requerido**): uno o varios números de registro AEMPS; repite el parámetro por cada valor.  
- `filename` (str, **requerido**): nombre del archivo HTML deseado (p.ej. "FichaTecnica.html").

**Comportamiento**  
- **Registro único** (`len(nregistro)==1`): devuelve un `StreamingResponse` con `media_type="text/html"` y el contenido HTML.
- **Múltiples registros**: genera en paralelo el HTML de cada ficha y devuelve un archivo ZIP con las páginas.
"""

html_ft_description = """
Obtiene el HTML completo de la ficha técnica de un único medicamento.

**Uso**  
```
GET /doc-html/ft/{nregistro}/{filename}
```

**Parámetros**  
- `nregistro` (str, **requerido**): número de registro AEMPS.  
- `filename` (str, **requerido**): nombre de archivo HTML (p.ej. "FichaTecnica.html").

"""

html_p_multiple_description = """
Descarga o devuelve el HTML completo del prospecto para uno o varios medicamentos.

**Uso**  
- Varios registros: `GET /doc-html/p?nregistro=AAA&nregistro=BBB&filename=Prospecto.html`  
- Único registro: si solo se incluye un `nregistro`, se devuelve directamente el HTML.

**Parámetros**  
- `nregistro` (List[str], **requerido**): uno o varios números de registro AEMPS; repite el parámetro por cada valor.  
- `filename` (str, **requerido**): nombre de archivo HTML deseado (p.ej. "Prospecto.html" o sección específica).

**Comportamiento**  
- **Registro único** (`len(nregistro)==1`): devuelve un `StreamingResponse` con `media_type="text/html"` y el contenido HTML.
- **Múltiples registros**: genera en paralelo el HTML de cada prospecto y devuelve un archivo ZIP con las páginas.
"""

html_p_description = """
Obtiene el HTML completo del prospecto para un único medicamento.

**Uso**  
```
GET /doc-html/p/{nregistro}/{filename}
```

**Parámetros**  
- `nregistro` (str, **requerido**): número de registro AEMPS.  
- `filename` (str, **requerido**): nombre de archivo HTML (p.ej. "Prospecto.html" o sección específica).
"""

descargar_ipt_description = """
Descarga los Informes de Posicionamiento Terapéutico (IPT) en PDF para uno o varios medicamentos.

**Uso**  
- Envía uno o varios parámetros `cn` o `nregistro` como consulta GET:  
  - Ejemplo único por CN: `GET /descargar-ipt?cn=123456`  
  - Ejemplo múltiple por CN: `GET /descargar-ipt?cn=123&cn=456`  
  - Ejemplo único por NRegistro: `GET /descargar-ipt?nregistro=AB-2025`  
  - Ejemplo múltiple por NRegistro: `GET /descargar-ipt?nregistro=AB-2025&nregistro=CD-2025`  
- Opcional: `timeout` (int): tiempo de espera en segundos para cada descarga (por defecto 15).

**Parámetros**  
- `cn` (List[str], opcional): uno o varios Códigos Nacionales (repetir por cada valor).  
- `nregistro` (List[str], opcional): uno o varios Números de Registro (repetir por cada valor).  
- **Requerido**: al menos uno de `cn` o `nregistro`.  
- `timeout` (int, opcional): timeout en segundos.

**Ejemplos**  
```
GET /descargar-ipt?cn=123&cn=456&timeout=20
GET /descargar-ipt?nregistro=AB-2025&nregistro=CD-2025
GET /descargar-ipt?cn=123&nregistro=AB-2025
```
"""

identificar_medicamento_description = """
Identifica hasta 10 presentaciones de medicamentos usando filtros y paginación.

**Uso**  
Envía los filtros como parámetros de consulta en un GET a `/identificar-medicamento`:
- `nregistro` (str): coincidencia exacta del Nº Registro AEMPS.
- `cn`        (str): coincidencia exacta del Código Nacional.
- `nombre`    (str): coincidencia parcial o difusa en el nombre de la presentación.
- `laboratorio`   (str): coincidencia parcial en el laboratorio fabricante.
- `atc`            (str): coincidencia parcial en el código ATC.
- `estado`         (str): coincidencia parcial en el estado.
- `comercializado` (bool): `true`/`false` para filtrar por comercializado.
- `pagina`    (int, ≥1; opcional): página de resultados (por defecto 1).
- `page_size` (int, 1–100; opcional): tamaño de página (por defecto 10).

**Parámetros obligatorios**  
- Al menos uno de `nregistro`, `cn` o `nombre`.

**Comportamiento**  
- Filtra el fichero `Presentaciones.xls` según los parámetros.
- Para `nombre`, aplica búsqueda difusa si no hay coincidencias directas.
- Devuelve hasta `page_size` resultados paginados.
"""

nomenclator_description = """
Realiza búsquedas avanzadas en el Nomenclátor de facturación de productos farmacéuticos.

**Uso**  
- Envía los filtros como parámetros de consulta en un GET a `/nomenclator`.
- Si no se especifica ningún filtro, devuelve todos los registros (paginados).
- `pagina` (int; ≥1) indica la página de resultados (por defecto 1).
- `page_size` (int; 1–100) indica el número de resultados por página (por defecto 10).

**Parámetros disponibles** (todos opcionales):
- `codigo_nacional` (str): coincidencia exacta del Código Nacional.
- `nombre_producto` (str): coincidencia parcial en el nombre del producto (case-insensitive).
- `tipo_farmaco` (str): coincidencia parcial en el tipo de fármaco.
- `principio_activo` (str): coincidencia parcial en el principio activo o asociación.
- `codigo_laboratorio` (str): coincidencia exacta del código de laboratorio ofertante.
- `nombre_laboratorio` (str): coincidencia parcial en el nombre del laboratorio.
- `estado` (str): coincidencia parcial en el estado (ej. "ALTA", "BAJA").
- `fecha_alta_desde` (str): fecha de alta ≥ dd/mm/yyyy.
- `fecha_alta_hasta` (str): fecha de alta ≤ dd/mm/yyyy.
- `fecha_baja_desde` (str): fecha de baja ≥ dd/mm/yyyy.
- `fecha_baja_hasta` (str): fecha de baja ≤ dd/mm/yyyy.
- `aportacion_beneficiario` (str): coincidencia parcial en la aportación del beneficiario.
- `precio_min_iva` (float): precio venta público mínimo con IVA.
- `precio_max_iva` (float): precio venta público máximo con IVA.
- `agrupacion_codigo` (str): coincidencia exacta del código de agrupación homogénea.
- `agrupacion_nombre` (str): coincidencia parcial en el nombre de agrupación homogénea.
- `diagnostico_hospitalario` (bool): `true` para sólo diagnóstico hospitalario.
- `larga_duracion` (bool): `true` para tratamiento de larga duración.
- `especial_control` (bool): `true` para especial control médico.
- `medicamento_huerfano` (bool): `true` para medicamento huérfano.
"""

descargar_imagenes_description = """
Descarga imágenes en alta resolución de la forma farmacéutica y/o del material de caja para uno o varios medicamentos.

**Uso**  
Envía los parámetros como consulta GET al endpoint `/descargar-imagenes`:
- `cn` (list[str], **requerido**): uno o varios Códigos Nacionales (`?cn=123&cn=456`).
- `tipos` (list[str], opcional): colecciones a descargar. Valores permitidos:
  - `formafarmac`  (forma farmacéutica)
  - `materialas`   (material de caja/packaging)
  (por defecto: ambos).
- `timeout` (int, opcional): tiempo de espera en segundos para cada descarga individual (por defecto 15).

**Ejemplo**  
```
GET /descargar-imagenes?cn=762540&cn=720186&tipos=formafarmac&timeout=10
```
"""

system_info_prompt_description = """
Devuelve el `MCP_AEMPS_SYSTEM_PROMPT`, que contiene:
- Descripción completa de las herramientas MCP disponibles.
- Flujo recomendado para el uso de cada una.
- Pautas y descargos de responsabilidad para las respuestas producidas por el agente.

**Uso**:
- Invoca este endpoint para obtener el prompt base que utiliza el agente farmacéutico digital.
"""
