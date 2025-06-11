# 💊 MCP-AEMPS Server

**Servidor MCP (Model Context Protocol) para la API de CIMA de la AEMPS**

Un servidor FastAPI que proporciona acceso programático a la información de medicamentos del Centro de Información de Medicamentos Autorizados (CIMA) de la Agencia Española de Medicamentos y Productos Sanitarios (AEMPS).

> ⚠️ **Nota importante**: Este es un servidor **NO OFICIAL**. La información oficial siempre está disponible en [cima.aemps.es](https://cima.aemps.es/cima/publico/home.html)

## 🚀 Características

- **API REST** completa para consultar medicamentos autorizados en España
- **Interfaz CLI** completa para gestión del servidor
- **Caché Redis** integrado para optimizar el rendimiento
- **Rate limiting** para proteger los recursos
- **Autenticación** opcional con JWT
- **Documentación automática** con Swagger UI
- **Contenerización** con Docker y Docker Compose
- **Modo desarrollo** con recarga automática

## 📋 Requisitos

### Requisitos mínimos
- **Python 3.12+**
- **Redis** (opcional, para caché)

### Para desarrollo
- **Docker** y **Docker Compose** (recomendado)
- **Poetry** (opcional, para gestión de dependencias)

## 🛠️ Instalación

### Opción 1: Docker Compose (Recomendado)

```bash
# Clonar el repositorio
git clone <tu-repositorio>
cd mcp_aemps

# Construir y levantar los servicios
docker-compose up -d

# El servidor estará disponible en http://localhost:8000
```

### Opción 2: Instalación local

```bash
# Clonar el repositorio
git clone <tu-repositorio>
cd mcp_aemps

# Crear entorno virtual
python -m venv venv
source venv/bin/activate  # En Windows: venv\Scripts\activate

# Instalar dependencias
pip install -r requirements.txt

# Instalar la aplicación en modo editable
pip install -e .

# Arrancar el servidor
mcp_aemps up
```

### Opción 3: Con Poetry

```bash
# Instalar dependencias con Poetry
poetry install

# Activar el entorno virtual
poetry shell

# Arrancar el servidor
mcp_aemps up
```

## 🎯 Uso del CLI

El servidor incluye una interfaz de línea de comandos completa para su gestión:

### Comandos principales

#### `mcp_aemps up`
Arranca el servidor en modo producción (sin autoreload):

```bash
# Arranque básico
mcp_aemps up

# Personalizar host y puerto
mcp_aemps up --uvicorn-host 0.0.0.0 --port 8080

# Ejecutar en background (daemon)
mcp_aemps up --daemon

# Más workers para mayor concurrencia
mcp_aemps up --workers 4
```

**Opciones disponibles:**
- `--uvicorn-host`: Dirección donde bindeará Uvicorn (por defecto: `0.0.0.0`)
- `--access-host`: Host público para acceder a la API (por defecto: `localhost`)
- `--port`: Puerto TCP (por defecto: `8000`)
- `--workers`: Número de workers Uvicorn (por defecto: `2`)
- `--log-level`: Nivel de log (por defecto: `info`)
- `--daemon`: Ejecutar en background

#### `mcp_aemps dev`
Arranca el servidor en modo desarrollo con recarga automática:

```bash
# Modo desarrollo con recarga automática
mcp_aemps dev

# En puerto específico
mcp_aemps dev --port 8080
```

#### `mcp_aemps down`
Detiene el servidor iniciado con `--daemon`:

```bash
mcp_aemps down
```

#### `mcp_aemps status`
Verifica si el servidor está ejecutándose:

```bash
mcp_aemps status
```

#### `mcp_aemps restart`
Reinicia el servidor manteniendo la configuración:

```bash
mcp_aemps restart
```

### Comandos de utilidad

#### `mcp_aemps health`
Consulta el endpoint `/health` del servidor:

```bash
mcp_aemps health
```

#### `mcp_aemps docs`
Abre la documentación Swagger UI en el navegador:

```bash
mcp_aemps docs
```

#### `mcp_aemps openapi`
Descarga la especificación OpenAPI:

```bash
# Guardar como openapi.json
mcp_aemps openapi

# Guardar con nombre personalizado
mcp_aemps openapi --output mi-spec.json
```

## 🐳 Docker

### Dockerfile

El proyecto incluye un Dockerfile optimizado que:

1. **Usa Python 3.12 slim** como base
2. **Instala dependencias del sistema** (libmagic1, jq)
3. **Configura el entorno** Python sin warnings
4. **Instala dependencias** Python desde requirements.txt
5. **Copia el código fuente** y datos necesarios
6. **Instala la aplicación** en modo editable
7. **Arranca automáticamente** usando la configuración del CLI

### Arranque automático

El contenedor arranca automáticamente con:

```bash
# El Dockerfile lee la configuración de mcp_aemps.json
HOST=$(jq -r .uvicorn_host /app/mcp_aemps.json)
PORT=$(jq -r .port /app/mcp_aemps.json)
exec mcp_aemps up --host "$HOST" --port "$PORT"
```

Esto permite configurar el servidor editando `app/mcp_aemps.json`:

```json
{
  "uvicorn_host": "0.0.0.0",
  "access_host": "localhost", 
  "port": 8000,
  "redis_url": "redis://localhost:6379/0",
  "allowed_origins": ["*"],
  "cache_prefix": "fastapi-cache",
  "data_dir": "data"
}
```

### Docker Compose

El `docker-compose.yml` incluye:

- **Servidor MCP-AEMPS**: El servidor principal en el puerto 8000
- **Redis**: Cache backend en el puerto 6379
- **Volúmenes**: Monta la carpeta `./data` para persistencia

```bash
# Levantar todos los servicios
docker-compose up -d

# Ver logs
docker-compose logs -f mcp_server

# Parar servicios
docker-compose down
```

## 📁 Estructura del proyecto

```
mcp_aemps/
├── app/
│   ├── cli.py              # CLI principal
│   ├── mcp_aemps_server.py # Servidor FastAPI
│   ├── mcp_aemps.json      # Configuración
│   └── dependencies.py    # Dependencias (git-ignored)
├── data/                   # Datos y assets
│   ├── assets/            # Logos e imágenes
│   ├── documentacion/     # Documentación (git-ignored)
│   ├── img/              # Imágenes (git-ignored)
│   └── pdf/              # PDFs (git-ignored)
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── pyproject.toml
└── README.md
```

## ⚙️ Configuración

### Variables de entorno

El servidor lee configuración desde `app/mcp_aemps.json`:

```json
{
  "uvicorn_host": "0.0.0.0",        // Host de binding de Uvicorn
  "access_host": "localhost",        // Host público de acceso
  "port": 8000,                     // Puerto TCP
  "redis_url": "redis://localhost:6379/0",  // URL de Redis
  "allowed_origins": ["*"],         // CORS origins permitidos
  "cache_prefix": "fastapi-cache",  // Prefijo para cache
  "data_dir": "data"               // Directorio de datos
}
```

### Gestión automática de puertos

Si el puerto configurado está ocupado, el CLI automáticamente:

1. **Busca el siguiente puerto libre**
2. **Actualiza la configuración** automáticamente
3. **Informa del cambio** al usuario

```bash
⚠️  El puerto 8000 está ocupado; usando puerto libre 8001.
🚀  Servidor en marcha → http://localhost:8001
```

## 🔍 API Endpoints

Una vez iniciado el servidor, puedes acceder a:

- **Swagger UI**: `http://localhost:8000/docs`
- **ReDoc**: `http://localhost:8000/redoc`
- **OpenAPI spec**: `http://localhost:8000/openapi.json`
- **Health check**: `http://localhost:8000/health`

## 🔧 Desarrollo

### Modo desarrollo

```bash
# Arrancar con recarga automática
mcp_aemps dev

# El servidor se reiniciará automáticamente al cambiar código
```

### Estructura de logs

Los logs incluyen información detallada:

```bash
# Ver logs en tiempo real (si usas archivo de log)
mcp_aemps logs --file server.log
```

### Testing

```bash
# Ejecutar tests (si los hay)
pytest tests/

# Los tests están git-ignored por configuración
```

## 🤝 Contribuir

1. Fork del repositorio
2. Crear una rama feature (`git checkout -b feature/nueva-funcionalidad`)
3. Commit de cambios (`git commit -am 'Añadir nueva funcionalidad'`)
4. Push a la rama (`git push origin feature/nueva-funcionalidad`)
5. Abrir un Pull Request

## 📄 Licencia

Este proyecto es **no oficial** y se proporciona tal como está. 

La información oficial sobre medicamentos siempre debe consultarse en:
- **CIMA-AEMPS**: https://cima.aemps.es/cima/publico/home.html
- **AEMPS**: https://www.aemps.gob.es/

## 🆘 Soporte

Para problemas o sugerencias:

1. **Revisar la documentación** en `/docs`
2. **Comprobar el health check**: `mcp_aemps health`
3. **Ver logs** para diagnóstico
4. **Abrir un issue** en GitHub

---

**Autor**: Román Pérez Dumpert  
**Email**: roman.p98@gmail.com