"""
FACTURAMI - Modelo de autorizacion (Fase 1).
===========================================
Todo endpoint de datos debe usar estas piezas. El servidor NUNCA confia en
cuenta_id / client_id / rol enviados por el cliente: la identidad sale
exclusivamente del JWT y la pertenencia de un cliente se consulta en SQLite.

  auth_required        -> exige JWT valido y cuenta activa (401 si no)
  role_required(...)   -> exige JWT valido y rol permitido (403 si no)
  can_access_cliente   -> el cliente pertenece a la cuenta del token
  deny_unless_cliente  -> atajo que devuelve la respuesta 400/403 o None

Decision de negocio: el rol 'admin' gestiona cuentas, planes y suscripciones,
pero NO tiene acceso al contenido contable de los clientes de los contadores.
Se controla con ADMIN_CONTENT_ACCESS (por defecto False). Si algun dia se
necesita acceso de soporte, activarlo de forma explicita y con bitacora.
"""
import re
from functools import wraps

from flask import jsonify
from flask_jwt_extended import verify_jwt_in_request, get_jwt, get_jwt_identity

import db as database

ADMIN_CONTENT_ACCESS = False

# Identificadores de cliente validos: cedulas y codigos alfanumericos simples.
CLIENT_ID_RE = re.compile(r'[A-Za-z0-9_-]{1,64}')


def _json_error(message, status):
    return jsonify({'ok': False, 'error': message}), status


def _cuenta_activa(username):
    """
    False si la cuenta no existe o esta suspendida (activo = 0 o active = False).
    Soporta cuentas tanto en la base de datos SQLite (tabla cuentas) como
    cuentas definidas exclusivamente en users.json (como cuentas iniciales o superadmin).
    """
    if not username:
        return False
    u_clean = str(username).strip().lower()

    # 1. Verificar primero en base de datos SQLite
    try:
        with database.get_db() as conn:
            row = conn.execute(
                "SELECT activo FROM cuentas WHERE LOWER(TRIM(username)) = ?", (u_clean,)
            ).fetchone()
            if row is not None:
                return row['activo'] is None or int(row['activo']) != 0
    except Exception:
        # Si la base de datos no responde, fallar cerrado
        return False

    # 2. Si no esta en la tabla cuentas, verificar si existe en users.json
    try:
        from services.auth_service import _load_users
        users = _load_users()
        u = next((
            x for x in users
            if (x.get('username') or '').strip().lower() == u_clean
            or (x.get('email') and x.get('email').strip().lower() == u_clean)
        ), None)
        if u is not None:
            return bool(u.get('active', True))
    except Exception:
        return False

    # Si no existe en ningun registro, rechazar acceso
    return False


def auth_required(fn):
    """401 si falta el token, es invalido o la cuenta esta inactiva/no existe."""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        verify_jwt_in_request()
        if not _cuenta_activa(get_jwt_identity()):
            return _json_error('Cuenta inactiva', 401)
        return fn(*args, **kwargs)
    return wrapper


def role_required(*roles):
    """403 si el rol del token no esta en `roles`. Implica auth_required."""
    def deco(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            verify_jwt_in_request()
            if not _cuenta_activa(get_jwt_identity()):
                return _json_error('Cuenta inactiva', 401)
            if get_jwt().get('role') not in roles:
                return _json_error('Forbidden', 403)
            return fn(*args, **kwargs)
        return wrapper
    return deco


def current_username():
    return get_jwt_identity()


def is_admin():
    return get_jwt().get('role') == 'admin'


def valid_client_id(cliente_id):
    return bool(cliente_id) and CLIENT_ID_RE.fullmatch(str(cliente_id)) is not None


def can_access_cliente(cliente_id):
    """True si el cliente pertenece a la cuenta del token (insensible a mayusculas)."""
    if not valid_client_id(cliente_id):
        return False
    if is_admin() and ADMIN_CONTENT_ACCESS:
        return True
    with database.get_db() as conn:
        row = conn.execute(
            "SELECT 1 FROM clientes WHERE id = ? AND LOWER(TRIM(cuenta_id)) = LOWER(TRIM(?))",
            (str(cliente_id), str(get_jwt_identity()))
        ).fetchone()
    return row is not None


def deny_unless_cliente(cliente_id):
    """Devuelve una respuesta de error (400/403) o None si el acceso es valido.

    Responde 403 tanto si el cliente no existe como si es ajeno, para no
    revelar que identificadores existen.
    """
    if not valid_client_id(cliente_id):
        return _json_error('Cliente invalido', 400)
    if not can_access_cliente(cliente_id):
        return _json_error('Acceso denegado al cliente', 403)
    return None
