"""
FACTURAMI - Rutas de Facturas.
Blueprint: /api/facturas/*
"""
import os
from functools import wraps

from flask import Blueprint, request, jsonify, send_file, redirect
from flask_jwt_extended import verify_jwt_in_request

from errors import success, error, created, conflict, handle_errors
from services.auth_service import validate_n8n_token
from services.queue_service import get_pendientes, count_pendientes, agregar, absorber, eliminar, get_by_id
from services.upload_service import upload_to_n8n, check_n8n_status
import db as database

facturas_bp = Blueprint('facturas', __name__)

LOCAL_ADDRS = ('127.0.0.1', '::1', 'localhost')


def local_or_jwt(fn):
    """Permite peticiones tanto locales como desde el frontend web de FACTURAMI.
    Si se envia un token JWT lo valida, pero si no se envia no bloquea la peticion.
    """
    @wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            verify_jwt_in_request(optional=True)
        except Exception:
            pass
        return fn(*args, **kwargs)
    return wrapper


# ══════════════════════════════════════════════════════════════════════════
#  Cola de facturas
# ══════════════════════════════════════════════════════════════════════════

@facturas_bp.route('/api/facturas/queue', methods=['GET'])
@local_or_jwt
@handle_errors
def get_queue():
    """GET /api/facturas/queue?client_id=X - Facturas pendientes de un cliente o todas las pendientes."""
    client_id = request.args.get('client_id')
    pendientes = get_pendientes(client_id)
    # Frontend espera array directo, no {ok:true, data:[]}
    return jsonify(pendientes)


@facturas_bp.route('/api/facturas/queue', methods=['POST'])
@handle_errors
def add_to_queue():
    """POST /api/facturas/queue - Recibe una factura desde n8n.
    
    No requiere JWT. Se valida con X-N8N-Token o localhost.
    """
    # Validar acceso n8n
    n8n_token = request.headers.get('X-N8N-Token', '')
    remote_addr = request.remote_addr or ''
    if not validate_n8n_token(n8n_token, remote_addr):
        return error('Token invalido', 403)

    data = request.get_json(force=True, silent=True)
    if not data:
        return error('Se requiere un body JSON', 400)

    ok, result = agregar(data)
    if not ok:
        return conflict(result, duplicado=True)

    return created({'id': result})


def check_quota_for_client(client_id):
    """Verifica si la cuenta a la que pertenece el cliente superó su cuota mensual."""
    if not client_id or client_id == 'default':
        return True, None
    with database.get_db() as conn:
        cli = conn.execute("SELECT cuenta_id FROM clientes WHERE id=?", (client_id,)).fetchone()
        if not cli or not cli['cuenta_id']:
            return True, None
        cuenta_id = cli['cuenta_id']
        cuenta = database.get_cuenta(conn, cuenta_id)
        if not cuenta:
            return True, None
        if cuenta.get('role') == 'admin' or cuenta.get('plan_id') == 'master':
            return True, None
        limite = int(cuenta.get('limite_facturas_mes') or 3000) + int(cuenta.get('creditos_extra') or 0)
        consumidas = int(cuenta.get('facturas_mes') or 0)
        if consumidas >= limite:
            return False, f"Límite mensual alcanzado ({consumidas}/{limite} facturas). Contacte a FACTURAMI para ampliar su plan."
        return True, None


@facturas_bp.route('/api/facturas/queue/<factura_id>', methods=['DELETE'])
@local_or_jwt
@handle_errors
def delete_from_queue(factura_id):
    """DELETE /api/facturas/queue/:id - Absorbe o elimina una factura."""
    modo = request.args.get('modo', 'marcar')
    if modo == 'eliminar':
        eliminar(factura_id)
    else:
        with database.get_db() as conn:
            fq = database.queue_get_by_id(conn, factura_id)
            if fq:
                allowed, msg = check_quota_for_client(fq.get('cliente_id'))
                if not allowed:
                    return error(msg, 403)
        absorber(factura_id)
    return success(message='Factura procesada')


@facturas_bp.route('/api/facturas/queue/count', methods=['GET'])
@local_or_jwt
@handle_errors
def queue_count():
    """GET /api/facturas/queue/count?client_id=X - Conteo para badge del sidebar."""
    client_id = request.args.get('client_id')
    count = count_pendientes(client_id)
    # Frontend espera {count: N} directo
    return jsonify({'count': count})


# ══════════════════════════════════════════════════════════════════════════
#  Upload PDF
# ══════════════════════════════════════════════════════════════════════════

@facturas_bp.route('/api/facturas/upload', methods=['POST'])
@local_or_jwt
@handle_errors
def upload_pdf():
    """POST /api/facturas/upload - Envia PDFs al webhook de n8n."""
    if 'files' not in request.files:
        return error('No se enviaron archivos', 400)

    files = request.files.getlist('files')
    if not files or files[0].filename == '':
        return error('Archivos vacios', 400)

    custom_url = request.form.get('webhook_url')
    client_id = request.form.get('client_id', 'default')

    allowed, msg = check_quota_for_client(client_id)
    if not allowed:
        return error(msg, 403)

    result = upload_to_n8n(files, client_id, custom_url)
    status = 200 if result['ok'] else 207
    # Frontend espera el dict directo con ok/procesados/errores
    return jsonify(result), status


@facturas_bp.route('/api/facturas/upload/status', methods=['GET'])
@local_or_jwt
@handle_errors
def upload_status():
    """GET /api/facturas/upload/status - Verifica disponibilidad de n8n."""
    result = check_n8n_status()
    return success(result)


# ══════════════════════════════════════════════════════════════════════════
#  Visualizacion de PDF (Local / Drive)
# ══════════════════════════════════════════════════════════════════════════

@facturas_bp.route('/api/facturas/<factura_id>/pdf', methods=['GET'])
@local_or_jwt
@handle_errors
def get_factura_pdf(factura_id):
    """GET /api/facturas/:id/pdf - Devuelve el PDF local o redirige a Google Drive para preview embebido."""
    factura = get_by_id(factura_id)
    if not factura:
        return error('Factura no encontrada', 404)

    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    storage_root = os.environ.get('FACTURAMI_STORAGE_PATH', 'storage')
    if not os.path.isabs(storage_root):
        storage_root = os.path.join(base_dir, storage_root)
    client_id = str(factura.get('cliente_id') or 'default')
    file_hash = str(factura.get('hash_archivo') or '').strip()

    # 1. Intentar archivo local por hash SHA-256 en la carpeta aislada del cliente (PDF o Imagen)
    if file_hash:
        target_path = None
        target_mime = 'application/pdf'
        cand_tuples = (
            ('.pdf', 'application/pdf'),
            ('.jpg', 'image/jpeg'),
            ('.jpeg', 'image/jpeg'),
            ('.png', 'image/png'),
            ('.webp', 'image/webp')
        )
        for cand_ext, cand_mime in cand_tuples:
            cand_p = os.path.join(storage_root, client_id, f"{file_hash}{cand_ext}")
            if os.path.isfile(cand_p):
                target_path = cand_p
                target_mime = cand_mime
                break

        # 2. Si no esta en la carpeta del cliente_id, buscar en el storage general
        if not target_path:
            for root, _, files in os.walk(storage_root):
                for f in files:
                    if f.startswith(file_hash):
                        target_path = os.path.join(root, f)
                        _, f_ext = os.path.splitext(f)
                        f_ext = f_ext.lower()
                        if f_ext in ('.jpg', '.jpeg'): target_mime = 'image/jpeg'
                        elif f_ext == '.png': target_mime = 'image/png'
                        elif f_ext == '.webp': target_mime = 'image/webp'
                        else: target_mime = 'application/pdf'
                        break
                if target_path:
                    break

        if target_path and os.path.isfile(target_path):
            ext_found = os.path.splitext(target_path)[1]
            return send_file(
                target_path,
                mimetype=target_mime,
                as_attachment=False,
                download_name=f"factura_{factura.get('factura', 'doc')}{ext_found}"
            )

    # 3. Fallback a Google Drive si tiene drive_id
    drive_id = str(factura.get('drive_id') or '').strip()
    if drive_id and drive_id not in ('N/A', '—', 'None', ''):
        if drive_id.startswith('http'):
            return redirect(drive_id)
        # Formato de visualizacion embebible en iframe para Google Drive
        return redirect(f"https://drive.google.com/file/d/{drive_id}/preview")

    return error('Archivo PDF no disponible para esta factura', 404)


# ══════════════════════════════════════════════════════════════════════════
#  Endpoints Internos para Automatizaciones n8n
# ══════════════════════════════════════════════════════════════════════════

@facturas_bp.route('/api/internal/cliente-config/<client_id>', methods=['GET'])
@handle_errors
def internal_cliente_config(client_id):
    """GET /api/internal/cliente-config/:client_id - Devuelve parametros de automatizacion de un cliente."""
    n8n_token = request.headers.get('X-N8N-Token', '')
    if not validate_n8n_token(n8n_token, request.remote_addr or ''):
        return error('Token invalido', 403)

    with database.get_db() as conn:
        cfg = database.get_cliente_config(conn, client_id)
        if not cfg:
            return error(f'Cliente {client_id} no encontrado o no configurado', 404)
        return jsonify(cfg)


@facturas_bp.route('/api/internal/resolve-client', methods=['GET'])
@handle_errors
def internal_resolve_client():
    """GET /api/internal/resolve-client?email=X - Resuelve el tenant a partir del email destinatario."""
    n8n_token = request.headers.get('X-N8N-Token', '')
    if not validate_n8n_token(n8n_token, request.remote_addr or ''):
        return error('Token invalido', 403)

    email = request.args.get('email', '')
    from_email = request.args.get('from', '')
    with database.get_db() as conn:
        cfg = database.resolve_client_by_email(conn, email, from_email=from_email)
        if not cfg:
            return error('No se encontro un cliente activo para este correo', 404)
        return jsonify(cfg)


@facturas_bp.route('/api/internal/clientes-config', methods=['GET'])
@handle_errors
def internal_all_clientes_config():
    """GET /api/internal/clientes-config - Lista configuraciones de todos los clientes activos."""
    n8n_token = request.headers.get('X-N8N-Token', '')
    if not validate_n8n_token(n8n_token, request.remote_addr or ''):
        return error('Token invalido', 403)

    with database.get_db() as conn:
        rows = conn.execute("SELECT * FROM clientes_config WHERE activo=1 ORDER BY cliente_id").fetchall()
        return jsonify([dict(r) for r in rows])
