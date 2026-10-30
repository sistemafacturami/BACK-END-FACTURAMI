# 🚀 Guía de Despliegue en la Nube de FACTURAMI

Este manual describe paso a paso cómo desplegar **FACTURAMI** en un servidor VPS privado para acceder vía web desde cualquier dispositivo (PC, Mac, iPad, iPhone, Android), manteniendo el 100% de los datos y sin pagar suscripciones recurrentes de software.

---

## 1. Requisitos Previos

1. **Un Servidor VPS Linux (Ubuntu 22.04 o 24.04 LTS):**
   - **Proveedores recomendados:** Hetzner Cloud (Plan CX22 ~$4.50/mes), DigitalOcean ($6/mes) o Contabo ($5.50/mes).
   - **Especificaciones mínimas:** 2 vCPU, 2 GB a 4 GB RAM, 20 GB SSD.
2. **Un Nombre de Dominio propio:**
   - Ejemplo: `contabilidadlvm.com` o `facturami.cr`.
   - Si no tienes uno, puedes comprarlo en Namecheap, Porkbun o Cloudflare (~$8–$12/año).

---

## 2. Configuración de DNS (En tu proveedor de Dominio)

Crea dos registros de tipo **A** apuntando a la **IP pública** de tu nuevo VPS:

| Tipo | Nombre (Host) | Valor (Destino) | TTL |
| :--- | :--- | :--- | :--- |
| **A** | `facturami` | `TU_IP_PUBLICA_DEL_VPS` | Automático / 300 |
| **A** | `n8n` | `TU_IP_PUBLICA_DEL_VPS` | Automático / 300 |

*Ejemplo:* Si tu dominio es `contabilidadlvm.com`, tendrás:
- `facturami.contabilidadlvm.com` (Tu Sistema Contable)
- `n8n.contabilidadlvm.com` (Tu Consola de Automatización e IA)

---

## 3. Preparación del Servidor VPS

Conéctate a tu servidor por SSH (desde PowerShell, Terminal o PuTTY):

```bash
ssh root@TU_IP_PUBLICA_DEL_VPS
```

Actualiza el sistema e instala Docker y utilidades básicas con un solo comando:

```bash
apt update && apt upgrade -y
apt install -y curl git ufw sqlite3

# Instalar Docker oficial
curl -fsSL https://get.docker.com -o get-docker.sh
sh get-docker.sh
```

Configura el cortafuegos para permitir SSH, HTTP y HTTPS:

```bash
ufw allow OpenSSH
ufw allow 80/tcp
ufw allow 443/tcp
ufw allow 443/udp
ufw enable
```

---

## 4. Subir FACTURAMI al Servidor

Crea el directorio de la aplicación en el servidor:

```bash
mkdir -p /opt/facturami
cd /opt/facturami
```

### Opción A: Copiar directamente desde tu memoria USB / PC Windows

Desde PowerShell en tu PC local:

```powershell
# Subir todo el directorio a tu servidor VPS
scp -r E:\FACTURAMI_PORTABLE\* root@TU_IP_PUBLICA_DEL_VPS:/opt/facturami/
```

### Opción B: Mediante Git

Si tienes el repositorio en GitHub o GitLab:

```bash
git clone https://github.com/tu-usuario/facturami.git /opt/facturami
cd /opt/facturami
```

*(Si usas Git, recuerda copiar también tu archivo `facturami.db` actual mediante `scp` para no perder tus clientes y facturas registradas).*

---

## 5. Configurar Variables de Entorno (`.env`)

Dentro del servidor, en `/opt/facturami`:

```bash
cp .env.example .env
nano .env
```

Edita los siguientes campos:

1. `DOMAIN_NAME`: Pon tu dominio (ej: `facturami.contabilidadlvm.com`).
2. `N8N_DOMAIN`: Pon tu subdominio (ej: `n8n.contabilidadlvm.com`).
3. `ACME_EMAIL`: Tu correo personal para la emisión del certificado SSL Let's Encrypt.
4. `JWT_SECRET_KEY`: Genera una clave segura o usa una frase larga y secreta.
5. `GEMINI_API_KEY`: Tu clave de Google Gemini para lectura OCR de facturas.

Guarda los cambios con `Ctrl + O`, presiona `Enter` y sal con `Ctrl + X`.

---

## 6. Poner en Marcha el Servicio

Construye las imágenes e inicia los contenedores en segundo plano:

```bash
cd /opt/facturami
docker compose up -d --build
```

Verifica que los tres contenedores estén en estado `Up` (saludable):

```bash
docker compose ps
```

Deberás ver:
- `facturami_caddy`: Reverse proxy activo en puertos 80 y 443 con SSL automático.
- `facturami_app`: Backend Flask + Gunicorn (4 workers).
- `facturami_n8n`: Automatización e ingesta de facturas.

Para ver los registros en tiempo real:

```bash
docker compose logs -f
```

---

## 7. Acceso y Uso desde Dispositivos

1. **Desde tu navegador web (PC o Mac):**
   - Ingresa a: `https://facturami.tudominio.com`
   - Observarás el candado verde/seguro de HTTPS.
2. **Instalación como App Nativa (PWA):**
   - **Google Chrome / Edge (PC/Mac):** Haz clic en el ícono de "Instalar FACTURAMI" que aparece en el extremo derecho de la barra de direcciones.
   - **iPhone / iPad (Safari):** Presiona el botón de compartir (cuadrado con flecha hacia arriba) y selecciona **"Agregar a pantalla de inicio"**.
   - **Android (Chrome):** Toca el menú de tres puntos y presiona **"Instalar aplicación"** o **"Agregar a la pantalla principal"**.

¡Listo! Ya tienes una aplicación de escritorio o móvil dedicada con el logo oficial de FACTURAMI, pantalla completa y acceso seguro.

---

## 8. Automatización de Copias de Seguridad (Backups)

Configura una tarea programada para que el servidor respalde automáticamente la base de datos `facturami.db` y las facturas PDF todas las noches a las 3:00 AM:

```bash
chmod +x /opt/facturami/backup_cloud.sh

# Abrir el editor de tareas cron
crontab -e
```

Añade al final del archivo la siguiente línea:

```cron
0 3 * * * /opt/facturami/backup_cloud.sh >> /opt/facturami/logs/backup.log 2>&1
```

Los respaldos se almacenarán en `/opt/facturami/backups/` y se purgarán automáticamente pasados 14 días para no saturar el almacenamiento.

---

## 9. Mantenimiento y Comandos Útiles

- **Reiniciar el sistema:**
  ```bash
  docker compose restart
  ```
- **Detener el sistema:**
  ```bash
  docker compose down
  ```
- **Actualizar el código después de hacer cambios:**
  ```bash
  git pull  # o copiar archivos nuevos
  docker compose up -d --build
  ```
- **Descargar una copia de seguridad a tu computadora:**
  ```powershell
  scp root@TU_IP_PUBLICA_DEL_VPS:/opt/facturami/backups/facturami_*.db E:\FACTURAMI_PORTABLE\backups\
  ```
