"""
FACTURAMI - Servicio de Upload PDF.
Logica de negocio para enviar PDFs al webhook de n8n.
"""
import os
import requests


def get_webhook_url():
    """Obtiene la URL del webhook de n8n."""
    return os.environ.get('N8N_WEBHOOK_URL', 'http://localhost:5678/webhook/importar-facturas')


import hashlib
import db as database


def upload_to_n8n(files_list, client_id, custom_url=None):
    """
    Envia archivos PDF al webhook de n8n para procesamiento con almacenamiento local y hash.
    
    Retorna dict con {ok, procesados, errores, detalle}.
    """
    if custom_url and ('/webhook-test/' in custom_url or not custom_url.startswith('http')):
        custom_url = None
    target_url = custom_url if custom_url else get_webhook_url()
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    storage_root = os.environ.get('FACTURAMI_STORAGE_PATH', 'storage')
    if not os.path.isabs(storage_root):
        storage_root = os.path.join(base_dir, storage_root)
    client_storage = os.path.join(storage_root, str(client_id))
    os.makedirs(client_storage, exist_ok=True)

    resultados = []
    errores = []

    for f in files_list:
        fname = f.filename
        try:
            # Leer contenido y calcular hash
            file_bytes = f.read()
            if not file_bytes:
                errores.append({'file': fname, 'error': 'Archivo vacio (0 bytes)'})
                continue

            file_hash = hashlib.sha256(file_bytes).hexdigest()

            # Extraer extension y determinar MIME type (soporte PDF y Fotos/Imagenes)
            _, raw_ext = os.path.splitext(fname)
            ext = raw_ext.lower()
            if ext in ('.jpg', '.jpeg'):
                content_type = 'image/jpeg'
                ext = '.jpg'
            elif ext == '.png':
                content_type = 'image/png'
            elif ext == '.webp':
                content_type = 'image/webp'
            else:
                ext = '.pdf'
                content_type = 'application/pdf'

            # Respetar content_type si viene de navegador o dispositivo movil
            f_ct = getattr(f, 'content_type', '') or ''
            if 'image' in f_ct:
                content_type = f_ct
                if 'jpeg' in f_ct or 'jpg' in f_ct: ext = '.jpg'
                elif 'png' in f_ct: ext = '.png'
                elif 'webp' in f_ct: ext = '.webp'

            # Guardar copia local en E:\FACTURAMI_PORTABLE\storage\<client_id>\
            local_path = os.path.join(client_storage, f"{file_hash}{ext}")
            if not os.path.exists(local_path):
                with open(local_path, 'wb') as lf:
                    lf.write(file_bytes)

            # Verificar si ya existe en cola con este hash
            with database.get_db() as conn:
                if database.queue_check_duplicado(conn, client_id, fname, hash_archivo=file_hash):
                    errores.append({'file': fname, 'error': 'Esta factura ya fue subida y esta en cola o procesada'})
                    continue

            # Obtener configuracion del tenant para n8n
            tenant_cfg = {}
            with database.get_db() as conn:
                tenant_cfg = database.get_cliente_config(conn, client_id) or {}

            # Enviar a n8n
            headers = {}
            internal_token = os.environ.get('N8N_INTERNAL_SECRET') or os.environ.get('N8N_API_KEY')
            if internal_token:
                headers['X-N8N-Token'] = internal_token

            es_foto = ext in ('.jpg', '.jpeg', '.png', '.webp') or ('image' in content_type)
            post_data = {
                'client_id': client_id,
                'hash_archivo': file_hash,
                'origen': 'foto_manual' if es_foto else 'pdf_manual',
                'drive_folder_inbox': tenant_cfg.get('drive_folder_inbox', '1pGhmiNt6fAchr0ACk0dhWyIlLRVyZdK5'),
                'drive_folder_processed': tenant_cfg.get('drive_folder_processed', '19OBoRIXSdthtmuVMShgg8HuNwg4EWPV1'),
                'razon_social_prompt': tenant_cfg.get('razon_social_prompt', 'Contabilidad LVM')
            }

            print(f"[UPLOAD] -> {target_url}: {fname} (Cliente: {client_id}, Tipo: {content_type}, Hash: {file_hash[:8]}...)")
            resp = requests.post(
                target_url,
                data=post_data,
                files={'file': (fname, file_bytes, content_type)},
                headers=headers,
                timeout=120
            )

            if resp.status_code < 300:
                resultados.append({'file': fname, 'ok': True, 'hash': file_hash})
                print(f"[UPLOAD] OK: {fname}")
            else:
                errores.append({'file': fname, 'error': f'n8n respondio {resp.status_code}'})
                print(f"[UPLOAD] Error n8n ({resp.status_code}): {fname}")

        except requests.ConnectionError:
            errores.append({'file': fname, 'error': 'No se pudo conectar con n8n (puerto 5678 inactivo)'})
        except requests.Timeout:
            errores.append({'file': fname, 'error': 'Timeout esperando n8n (>120s)'})
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
            return {'ok': True, 'n8n': True, 'nota': 'n8n activo, workflow puede requerir activacion'}
        except Exception:
            return {'ok': False, 'n8n': False}

