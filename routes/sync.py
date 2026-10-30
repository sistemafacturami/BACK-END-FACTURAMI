"""
FACTURAMI - Rutas de Sincronizacion y Configuracion.
Blueprint: /api/sync/*, /api/config/*
"""
import os
from flask import Blueprint, request
from flask_jwt_extended import jwt_required

from errors import success, error, handle_errors
from services.sync_service import pull, push

sync_bp = Blueprint('sync', __name__)


@sync_bp.route('/api/sync/pull', methods=['GET'])
@jwt_required(optional=True)
@handle_errors
def pull_data():
    """GET /api/sync/pull - Entrega todos los datos para el frontend, filtrados por cuenta."""
    cuenta_id = request.args.get('cuenta_id')
    try:
        from flask_jwt_extended import get_jwt_identity, get_jwt
        ident = get_jwt_identity()
        claims = get_jwt()
        if ident and claims and claims.get('role') != 'admin':
            cuenta_id = ident
    except Exception:
        pass
    if not cuenta_id:
        return success({'empty': True, 'data': {'f_clients': []}, 'cuenta_id': None})
    data, is_empty = pull(cuenta_id=cuenta_id)
    return success({'empty': is_empty, 'data': data, 'cuenta_id': cuenta_id})


@sync_bp.route('/api/sync/push', methods=['POST'])
@jwt_required(optional=True)
@handle_errors
def push_data():
    """POST /api/sync/push - Persiste datos del frontend en SQLite y usb_database.json asociando la cuenta."""
    data = request.get_json(force=True, silent=True)
    if not data:
        return error('Se requiere un body JSON', 400)
    
    cuenta_id = request.args.get('cuenta_id') or data.get('cuenta_id')
    try:
        from flask_jwt_extended import get_jwt_identity, get_jwt
        ident = get_jwt_identity()
        claims = get_jwt()
        if ident and claims and claims.get('role') != 'admin':
            cuenta_id = ident
    except Exception:
        pass
    if not cuenta_id:
        return error('No se especificó cuenta_id', 400)
        
    push(data, cuenta_id=cuenta_id)
    return success(message='Sincronizacion completada')


@sync_bp.route('/api/config/public', methods=['GET'])
def public_config():
    """GET /api/config/public - Configuracion publica (sin secretos)."""
    return success({
        'n8n_webhook_url': os.environ.get('N8N_WEBHOOK_URL', ''),
        'version': '4.0.0-modular'
    })


# ══════════════════════════════════════════════════════════════════════════
#  Endpoints legacy (compatibilidad con versiones anteriores)
# ══════════════════════════════════════════════════════════════════════════

@sync_bp.route('/inbox', methods=['GET'])
def inbox_legacy():
    """Legacy: redirige a /api/facturas/queue."""
    from services.queue_service import get_pendientes
    pendientes = get_pendientes('default')
    return success(pendientes)


@sync_bp.route('/clear', methods=['POST'])
def clear_legacy():
    """Legacy: ya no borra todo."""
    return success(message='OK')
