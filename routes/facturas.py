"""
FACTURAMI - Rutas de Facturas.
Blueprint: /api/facturas/*

Autorizacion (Fase 1):
  - Endpoints de usuario: JWT obligatorio + el cliente debe pertenecer a la cuenta.
  - Endpoints de n8n: header X-N8N-Token contra N8N_INTERNAL_SECRET (sin bypass por IP).
"""
import os
import re

from flask import Blueprint, request, jsonify, send_file, redirect

from errors import success, error, created, conflict, handle_errors
from services.auth_service import validate_n8n_token
from services.authz import auth_required, deny_unless_cliente, valid_client_id
from services.queue_service import get_pendientes, count_pendientes, agregar, absorber, eliminar, get_by_id
from services.upload_service import upload_to_n8n, check_n8n_status
import db as database

facturas_bp = Blueprint('facturas', __name__)

HASH_RE = re.compile(r'[0-9a-f]{64}')
DRIVE_ID_RE = re.compile(r'[A-Za-z0-9_-]{10,128}')
DRIVE_HOSTS = ('https://drive.google.com/', 'https://docs.google.com/')
NO_CLIENT = ('', 'all', '*', 'default', None)


def _factura_o_error(factura_id):
    """Devuelve (factura, None) si existe y es del usuario, o (None, respuesta_error)."""
    factura = get_by_id(factura_id)
    if not factura:
        return None, error('Factura no encontrada', 404)
    denied = deny_unless_cliente(str(factura.get('cliente_id') or ''))
    if denied:
        # 404 en lugar de 403: no revelar que la factura existe.
        return None, error('Factura no encontrada', 404)
    return factura, None


# ══════════════════════════════════════════════════════════════════════════
#  Cola de facturas
# ══════════════════════════════════════════════════════════════════════════

@facturas_bp.route('/api/facturas/queue', methods=['GET'])
@auth_required
@handle_errors
def get_queue():
    """GET /api/facturas/queue?client_id=X - Facturas pendientes de UN cliente de la cuenta."""
    client_id = request.args.get('client_id')
    if client_id in NO_CLIENT:
        return jsonify([])
    denied = deny_unless_cliente(client_id)
    if denied:
        return denied
    # Frontend espera array directo, no {ok:true, data:[]}
    return jsonify(get_pendientes(client_id))


@facturas_bp.route('/api/facturas/queue', methods=['POST'])
@handle_errors
def add_to_queue():
    """POST /api/facturas/queue - Recibe una factura desde n8n (solo con X-N8N-Token)."""
    if not validate_n8n_token(request):
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
@auth_required
@handle_errors
def delete_from_queue(factura_id):
    """DELETE /api/facturas/queue/:id - Absorbe o elimina una factura propia."""
    fq, err = _factura_o_error(factura_id)
    if err:
        return err

    modo = request.args.get('modo', 'marcar')
    if modo == 'eliminar':
        eliminar(factura_id)
    else:
        allowed, msg = check_quota_for_client(fq.get('cliente_id'))
        if not allowed:
            return error(msg, 403)
        absorber(factura_id)
    return success(message='Factura procesada')


@facturas_bp.route('/api/facturas/queue/count', methods=['GET'])
@auth_required
@handle_errors
def queue_count():
    """GET /api/facturas/queue/count?client_id=X - Conteo para badge del sidebar."""
    client_id = request.args.get('client_id')
    if client_id in NO_CLIENT:
        return jsonify({'count': 0})
    denied = deny_unless_cliente(client_id)
    if denied:
        return denied
    # Frontend espera {count: N} directo
    return jsonify({'count': count_pendientes(client_id)})


# ══════════════════════════════════════════════════════════════════════════
#  Upload PDF / Foto
# ══════════════════════════════════════════════════════════════════════════

@facturas_bp.route('/api/facturas/upload', methods=['POST'])
@auth_required
@handle_errors
def upload_pdf():
    """POST /api/facturas/upload - Envia PDFs/fotos al webhook de n8n (URL fija por entorno)."""
    if 'files' not in request.files:
        return error('No se enviaron archivos', 400)

    files = request.files.getlist('files')
    if not files or files[0].filename == '':
        return error('Archivos vacios', 400)

    # El destino del webhook NUNCA viene del cliente (N8N_WEBHOOK_URL por entorno).
    client_id = request.form.get('client_id', '')
    denied = deny_unless_cliente(client_id)
    if denied:
        return denied

    allowed, msg = check_quota_for_client(client_id)
    if not allowed:
        return error(msg, 403)

    result = upload_to_n8n(files, client_id)
    status = 200 if result['ok'] else 207
    # Frontend espera el dict directo con ok/procesados/errores
    return jsonify(result), status


@facturas_bp.route('/api/facturas/upload/status', methods=['GET'])
@auth_required
@handle_errors
def upload_status():
    """GET /api/facturas/upload/status - Verifica disponibilidad de n8n."""
    return success(check_n8n_status())


# ══════════════════════════════════════════════════════════════════════════
#  Visualizacion de PDF (Local / Drive)
# ══════════════════════════════════════════════════════════════════════════

@facturas_bp.route('/api/facturas/<factura_id>/pdf', methods=['GET'])
@auth_required
@handle_errors
def get_factura_pdf(factura_id):
    """GET /api/facturas/:id/pdf - Devuelve el archivo local o redirige a Google Drive."""
    factura, err = _factura_o_error(factura_id)
    if err:
        return err

    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    storage_root = os.environ.get('FACTURAMI_STORAGE_PATH', 'storage')
    if not os.path.isabs(storage_root):
        storage_root = os.path.join(base_dir, storage_root)
    storage_root = os.path.realpath(storage_root)

    client_id = str(factura.get('cliente_id') or '')
    file_hash = str(factura.get('hash_archivo') or '').strip().lower()

    # 1. Archivo local por hash SHA-256 SOLO en la carpeta del cliente (sin buscar en otras).
    if valid_client_id(client_id) and HASH_RE.fullmatch(file_hash):
        client_dir = os.path.realpath(os.path.join(storage_root, client_id))
        if client_dir.startswith(storage_root + os.sep):
            for cand_ext, cand_mime in (('.pdf', 'application/pdf'), ('.jpg', 'image/jpeg'),
                                        ('.png', 'image/png'), ('.webp', 'image/webp'),
                                        ('.heic', 'image/heic')):
                cand_p = os.path.join(client_dir, f"{file_hash}{cand_ext}")
                if os.path.isfile(cand_p):
                    return send_file(
                        cand_p,
                        mimetype=cand_mime,
                        as_attachment=False,
                        download_name=f"factura_{re.sub(r'[^A-Za-z0-9_.-]', '_', str(factura.get('factura', 'doc')))}{cand_ext}"
                    )

    # 2. Fallback a Google Drive: solo hosts de Google (evita redireccion abierta).
    drive_id = str(factura.get('drive_id') or '').strip()
    if drive_id and drive_id not in ('N/A', '—', 'None', ''):
        if drive_id.startswith('http'):
            if drive_id.startswith(DRIVE_HOSTS):
                return redirect(drive_id)
        elif DRIVE_ID_RE.fullmatch(drive_id):
            return redirect(f"https://drive.google.com/file/d/{drive_id}/preview")

    return error('Archivo PDF no disponible para esta factura', 404)


# ══════════════════════════════════════════════════════════════════════════
#  Endpoints Internos para Automatizaciones n8n (solo X-N8N-Token)
# ══════════════════════════════════════════════════════════════════════════

@facturas_bp.route('/api/internal/cliente-config/<client_id>', methods=['GET'])
@handle_errors
def internal_cliente_config(client_id):
    """GET /api/internal/cliente-config/:client_id - Parametros de automatizacion de un cliente."""
    if not validate_n8n_token(request):
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
    if not validate_n8n_token(request):
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
    """GET /api/internal/clientes-config - Configuraciones de todos los clientes activos."""
    if not validate_n8n_token(request):
        return error('Token invalido', 403)

    with database.get_db() as conn:
        rows = conn.execute("SELECT * FROM clientes_config WHERE activo=1 ORDER BY cliente_id").fetchall()
        return jsonify([dict(r) for r in rows])
