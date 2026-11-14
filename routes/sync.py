"""
FACTURAMI - Rutas de Sincronizacion y Configuracion.
Blueprint: /api/sync/*, /api/config/*

Autorizacion (Fase 1):
  - Sync pull/push requieren token JWT valido (@auth_required).
  - La cuenta se toma exclusivamente de get_jwt_identity().
  - Se valida pertenencia de cada cliente antes de persistir f_data_<cid>.
"""
import os
from flask import Blueprint, request
from flask_jwt_extended import get_jwt_identity

from errors import success, error, handle_errors
from services.authz import auth_required, valid_client_id
from services.sync_service import pull, push
import db as database

sync_bp = Blueprint('sync', __name__)


@sync_bp.route('/api/sync/pull', methods=['GET'])
@auth_required
@handle_errors
def pull_data():
    """GET /api/sync/pull - Entrega todos los datos para el frontend, filtrados por la cuenta del token."""
    cuenta_id = get_jwt_identity()
    if not cuenta_id:
        return error('Identidad no encontrada en token', 401)

    data, is_empty = pull(cuenta_id=cuenta_id)

    return success({'empty': is_empty, 'data': data, 'cuenta_id': cuenta_id})


@sync_bp.route('/api/sync/push', methods=['POST'])
@auth_required
@handle_errors
def push_data():
    """POST /api/sync/push - Persiste datos del frontend asociando estrictamente la cuenta del token."""
    cuenta_id = get_jwt_identity()
    if not cuenta_id:
        return error('Identidad no encontrada en token', 401)

    data = request.get_json(force=True, silent=True)
    if not data or not isinstance(data, dict):
        return error('Se requiere un body JSON valido', 400)

    # Forzar cuenta_id del token (no confiar en data.get('cuenta_id'))
    data['cuenta_id'] = cuenta_id

    # La validacion de propiedad y consistencia se ejecuta atomicamente en db.sync_push (BEGIN IMMEDIATE)
    try:
        push(data, cuenta_id=cuenta_id)
    except PermissionError as pe:
        cid = str(pe).strip()
        if ' ' in cid:
            cid = cid.split()[-1].strip('()')
        return error(f"Acceso denegado a cliente {cid}", 403, cliente_id=cid)
    except ValueError as ve:
        return error(str(ve) or 'Datos de sincronizacion invalidos', 400)

    return success(message='Sincronizacion completada')


@sync_bp.route('/api/config/public', methods=['GET'])
def public_config():
    """GET /api/config/public - Configuracion publica (sin secretos ni URLs internas)."""
    return success({
        'version': '4.0.0-modular'
    })
