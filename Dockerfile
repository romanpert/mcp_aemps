FROM python:3.12-slim

# Establecer variables de entorno para evitar advertencias de pip y asegurar salida sin buffer
ENV PIP_ROOT_USER_ACTION=ignore
ENV PYTHONUNBUFFERED=1

# Dependencias de sistema
RUN apt-get update \
 && apt-get install -y --no-install-recommends libmagic1 \
 && apt-get install -y --no-install-recommends jq \
 && rm -rf /var/lib/apt/lists/*

# ----- 1 · Directorio raíz -----
WORKDIR /app

# ----- 2 · Copiar archivos de configuración y dependencias -----
COPY requirements.txt ./
COPY pyproject.toml ./

# ----- 3 · Instalar dependencias -----
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir -r requirements.txt

# ----- 3 · Copiamos código y spec -----
COPY app ./app
COPY data ./data

# ----- 5 · Instalar la aplicación en modo editable -----
RUN pip install --no-cache-dir -e .

# ----- 6 · Exponer y arrancar -----
EXPOSE 8000

# 3A) Arranque de Uvicorn
#    --app-dir /app indica a Uvicorn dónde buscar el módulo Python
# CMD ["uvicorn", "app/mcp_aemps_server:app", "--host", "0.0.0.0", "--port", "8000", "--app-dir", "/app"]

# 3B) Arranque con CLI
# CMD en exec-form: una sola línea, comillas dobles externas y escapadas internas
CMD ["sh", "-c", "HOST=$(jq -r .uvicorn_host /app/mcp_aemps.json) && \
PORT=$(jq -r .port /app/mcp_aemps.json) && \
echo \"Arrancando en $HOST:$PORT…\" && \
exec mcp_aemps up --host \"$HOST\" --port \"$PORT\""]