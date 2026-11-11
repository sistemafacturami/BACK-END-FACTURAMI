# ==============================================================================
# FACTURAMI - Dockerfile de Producción
# ==============================================================================
FROM python:3.11-slim

# Evitar prompts interactivos y optimizar Python
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    FLASK_PORT=5001

WORKDIR /app

# Instalar utilidades mínimas del sistema
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Instalar dependencias de Python con caché optimizado
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copiar el código fuente y activos
COPY . /app

# Crear carpetas requeridas para almacenamiento y persistencia
RUN mkdir -p /app/storage /app/tmp /app/backups && \
    groupadd -g 10001 facturami && \
    useradd -u 10001 -g facturami -d /app -s /usr/sbin/nologin facturami && \
    chown -R facturami:facturami /app

# Ejecutar como usuario sin privilegios
USER facturami

# Exponer el puerto del backend
EXPOSE 5001

# Verificación de salud del contenedor
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://127.0.0.1:5001/ || exit 1

# Servir mediante Gunicorn (4 workers, 2 hilos por worker, timeout 120s)
CMD ["gunicorn", "--workers", "4", "--threads", "2", "--bind", "0.0.0.0:5001", "--timeout", "120", "--access-logfile", "-", "--error-logfile", "-", "app:app"]
