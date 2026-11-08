"""
FACTURAMI - Servicio de Upload PDF / Imagen.
Logica de negocio para validar y enviar archivos al webhook interno de n8n.
"""
import hashlib
import os
import requests

from services.authz import valid_client_id
import db as database


def get_webhook_url():
    """Obtiene la URL del webhook de n8n exclusivamente desde variable de entorno."""
    return os.environ.get('N8N_WEBHOOK_URL', 'http://localhost:5678/webhook/importar-facturas')


def detect_file_type(file_bytes):
    """
    Detecta el tipo de archivo mediante magic bytes (firmas binarias).
    Rechaza archivos ejecutables, scripts o con extensiones falsificadas.
    Retorna (ext, content_type) o (None, None).
    """
    if len(file_bytes) < 4:
        return None, None
    if file_bytes.startswith(b'%PDF-'):
        return '.pdf', 'application/pdf'
    if file_bytes.startswith(b'\xff\xd8\xff'):
        return '.jpg', 'image/jpeg'
    if file_bytes.startswith(b'\x89PNG\r\n\x1a\n'):
        return '.png', 'image/png'
    if file_bytes.startswith(b'RIFF') and len(file_bytes) >= 12 and file_bytes[8:12] == b'WEBP':
        return '.webp', 'image/webp'
    return None, None


def upload_to_n8n(files_list, client_id):
    """
    Valida y envia archivos al webhook de n8n.
    - Rechaza client_id con path traversal o caracteres invalidos.
    - Valida magic bytes para PDF/JPG/PNG/WEBP.
    - Calcula SHA-256 y almacena en carpeta segura del cliente.
    - Envia al webhook con X-N8N-Token.
    """
    client_id_str = str(client_id or '').strip()
    if not valid_client_id(client_id_str):
        return {
            'ok': False,
            'procesados': 0,
            'errores': len(files_list),
            'detalle': [{'file': getattr(f, 'filename', ''), 'error': 'ID de cliente invalido'} for f in files_list]
        }

    target_url = get_webhook_url()
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    storage_root = os.environ.get('FACTURAMI_STORAGE_PATH', 'storage')
    if not os.path.isabs(storage_root):
        storage_root = os.path.join(base_dir, storage_root)
    storage_root = os.path.realpath(storage_root)

    client_storage = os.path.realpath(os.path.join(storage_root, client_id_str))
    if not client_storage.startswith(storage_root + os.sep):
        return {
            'ok': False,
            'procesados': 0,
            'errores': len(files_list),
            'detalle': [{'file': getattr(f, 'filename', ''), 'error': 'Ruta de almacenamiento invalida'} for f in files_list]
        }

    os.makedirs(client_storage, exist_ok=True)

    resultados = []
    errores = []

    for f in files_list:
        fname = getattr(f, 'filename', '') or 'archivo'
        try:
            file_bytes = f.read()
            if not file_bytes:
                errores.append({'file': fname, 'error': 'Archivo vacio (0 bytes)'})
                continue

            # Validar tipo de archivo por contenido binario real (magic bytes)
            ext, content_type = detect_file_type(file_bytes)
            if not ext:
                errores.append({
                    'file': fname,
                    'error': 'Formato no permitido. Solo se aceptan documentos PDF e imagenes JPG, PNG o WEBP'
                })
                continue

            file_hash = hashlib.sha256(file_bytes).hexdigest()

            # Guardar copia local en storage/<client_id>/<hash>.<ext>
            local_path = os.path.join(client_storage, f"{file_hash}{ext}")
            if not os.path.exists(local_path):
                with open(local_path, 'wb') as lf:
                    lf.write(file_bytes)

            # Verificar si ya existe en cola con este hash
            with database.get_db() as conn:
                if database.queue_check_duplicado(conn, client_id_str, fname, hash_archivo=file_hash):
                    errores.append({'file': fname, 'error': 'Esta factura ya fue subida y esta en cola o procesada'})
                    continue

            # Obtener configuracion del tenant para n8n
            tenant_cfg = {}
            with database.get_db() as conn:
                tenant_cfg = database.get_cliente_config(conn, client_id_str) or {}

            # Enviar a n8n
            headers = {}
            internal_token = os.environ.get('N8N_INTERNAL_SECRET') or os.environ.get('N8N_API_KEY')
            if internal_token:
                headers['X-N8N-Token'] = internal_token

            es_foto = ext in ('.jpg', '.jpeg', '.png', '.webp')
            post_data = {
                'client_id': client_id_str,
                'hash_archivo': file_hash,
                'origen': 'foto_manual' if es_foto else 'pdf_manual',
                'drive_folder_inbox': tenant_cfg.get('drive_folder_inbox', '1pGhmiNt6fAchr0ACk0dhWyIlLRVyZdK5'),
                'drive_folder_processed': tenant_cfg.get('drive_folder_processed', '19OBoRIXSdthtmuVMShgg8HuNwg4EWPV1'),
                'razon_social_prompt': tenant_cfg.get('razon_social_prompt', 'Contabilidad LVM')
            }

            resp = requests.post(
                target_url,
                data=post_data,
                files={'file': (fname, file_bytes, content_type)},
                headers=headers,
                timeout=120
            )

            if resp.status_code < 300:
                resultados.append({'file': fname, 'ok': True, 'hash': file_hash})
            else:
                errores.append({'file': fname, 'error': f'error de conexion {resp.status_code}'})

        except requests.ConnectionError:
            errores.append({'file': fname, 'error': 'error de conexion'})
        except requests.Timeout:
            errores.append({'file': fname, 'error': 'error de conexion (tiempo de espera)'})
        except Exception as e:
            errores.append({'file': fname, 'error': str(e)})

    return {
        'ok': len(errores) == 0,
        'procesados': len(resultados),
        'errores': len(errores),
        'detalle': resultados + errores
    }


def check_n8n_status():
    """Verifica si n8n esta disponible."""
    url = get_webhook_url()
    try:
        requests.get(url.replace('/webhook/', '/webhook-test/'), timeout=3)
        return {'ok': True, 'n8n': True}
    except Exception:
        try:
            n8n_base = os.environ.get('N8N_BASE_URL')
            if not n8n_base:
                from urllib.parse import urlparse
                p = urlparse(url)
                n8n_base = f"{p.scheme}://{p.netloc}" if p.netloc else 'http://localhost:5678'
            requests.get(f'{n8n_base}/healthz', timeout=3)
            return {'ok': True, 'n8n': True, 'nota': 'Servicio activo, workflow en espera de activacion'}
        except Exception:
            return {'ok': False, 'n8n': False}
