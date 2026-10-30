"""
FACTURAMI - Rutas de Administracion de Contadores y Suscripciones (SuperAdmin).
Blueprint: /api/admin/*
"""
import os
import json
import bcrypt
from flask import Blueprint, request
from flask_jwt_extended import jwt_required, get_jwt_identity, get_jwt

import db as database
from errors import success, error, handle_errors

admin_bp = Blueprint('admin', __name__)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
USERS_FILE = os.path.join(BASE_DIR, 'users.json')


def _load_users():
    if not os.path.exists(USERS_FILE):
        return []
    with open(USERS_FILE, 'r', encoding='utf-8') as f:
        return json.load(f)


def _save_users(users):
    with open(USERS_FILE, 'w', encoding='utf-8') as f:
        json.dump(users, f, ensure_ascii=False, indent=2)


PLANES_DEF = {
    'emprendedor': {'facturas': 100,  'max_clientes': 1,    'precio': 9,  'precio_crc': 4500,  'nombre': 'Plan Emprendedor (Independientes)'},
    'profesional': {'facturas': 350,  'max_clientes': 3,    'precio': 19, 'precio_crc': 9600,  'nombre': 'Plan Profesional (Pymes y Comercios)'},
    'corporativo': {'facturas': 1000, 'max_clientes': 10,   'precio': 39, 'precio_crc': 19700, 'nombre': 'Plan Corporativo (Empresas)'},
    'despacho':    {'facturas': 3000, 'max_clientes': 3000, 'precio': 79, 'precio_crc': 39900, 'nombre': 'Plan Despacho (Contadores)'},
    'master':      {'facturas': 999999, 'max_clientes': 999999, 'precio': 0, 'precio_crc': 0, 'nombre': 'Master Admin'}
}


@admin_bp.route('/api/admin/contadores', methods=['GET'])
@jwt_required(optional=True)
@handle_errors
def list_contadores():
    """GET /api/admin/contadores - Lista todas las cuentas de contadores con su consumo actual."""
    with database.get_db() as conn:
        cuentas = database.get_cuentas(conn)
    return success(cuentas)


@admin_bp.route('/api/admin/contadores', methods=['POST'])
@jwt_required(optional=True)
@handle_errors
def create_contador():
    """POST /api/admin/contadores - Crea un nuevo contador/despacho con su plan."""
    data = request.get_json(force=True, silent=True) or {}
    username = (data.get('username') or '').strip().lower()
    password = (data.get('password') or '').strip()
    nombre   = (data.get('nombre') or '').strip()
    email    = (data.get('email') or '').strip()
    telefono = (data.get('telefono') or '').strip()
    plan_id  = (data.get('plan_id') or 'despacho').strip().lower()

    if not username or not password:
        return error('Usuario y contraseña son requeridos', 400)

    if len(username) < 3:
        return error('El nombre de usuario debe tener al menos 3 caracteres', 400)

    users = _load_users()
    if any(u['username'].lower() == username for u in users):
        return error(f"El usuario '{username}' ya existe", 409)

    # Determinar limites segun el plan
    plan_meta = PLANES_DEF.get(plan_id, PLANES_DEF['despacho'])
    limite = int(data.get('limite_facturas_mes') or plan_meta['facturas'])
    max_cli = int(data.get('max_clientes') or plan_meta['max_clientes'])

    # Encriptar password
    salt = bcrypt.gensalt(rounds=12)
    pw_hash = bcrypt.hashpw(password.encode('utf-8'), salt).decode('utf-8')

    # Guardar en users.json
    users.append({
        'username': username,
        'password_hash': pw_hash,
        'role': 'contador',
        'nombre': nombre or username,
        'email': email,
        'plan_id': plan_id,
        'limite_facturas_mes': limite,
        'max_clientes': max_cli,
        'activo': 1
    })
    _save_users(users)

    # Guardar en base de datos SQLite
    cuenta_dict = {
        'username': username,
        'nombre': nombre or username,
        'email': email,
        'telefono': telefono,
        'plan_id': plan_id,
        'limite_facturas_mes': limite,
        'creditos_extra': 0,
        'max_clientes': max_cli,
        'activo': 1,
        'role': 'contador',
        'password_hash': pw_hash
    }
    with database.get_db() as conn:
        database.upsert_cuenta(conn, cuenta_dict)

    print(f"[ADMIN] Contador creado exitosamente: {username} ({plan_id})")
    return success(cuenta_dict, message=f"Contador '{nombre or username}' creado exitosamente", status=201)


@admin_bp.route('/api/admin/contadores/<username>', methods=['PUT'])
@jwt_required(optional=True)
@handle_errors
def update_contador(username):
    """PUT /api/admin/contadores/<username> - Actualiza plan, limites o estado."""
    data = request.get_json(force=True, silent=True) or {}
    users = _load_users()
    user = next((u for u in users if u.get('username', '').lower() == username.lower()), None)

    with database.get_db() as conn:
        cuenta = database.get_cuenta(conn, username)

    if not user and not cuenta:
        return error(f"Contador '{username}' no encontrado", 404)

    # Campos a actualizar
    if 'nombre' in data:
        nombre = data['nombre'].strip()
        if user: user['nombre'] = nombre
    else:
        nombre = (cuenta or {}).get('nombre') or (user or {}).get('nombre') or username

    email = data.get('email', (cuenta or {}).get('email', ''))
    telefono = data.get('telefono', (cuenta or {}).get('telefono', ''))
    plan_id = (data.get('plan_id') or (cuenta or {}).get('plan_id') or 'despacho').lower()
    
    plan_meta = PLANES_DEF.get(plan_id, PLANES_DEF['despacho'])
    limite = int(data.get('limite_facturas_mes') or plan_meta['facturas'])
    max_cli = int(data.get('max_clientes') or plan_meta['max_clientes'])
    creditos = int(data.get('creditos_extra', (cuenta or {}).get('creditos_extra', 0)))
    activo = int(data.get('activo', (cuenta or {}).get('activo', 1)))

    new_pw_hash = ''
    if 'password' in data and data['password'].strip():
        salt = bcrypt.gensalt(rounds=12)
        new_pw_hash = bcrypt.hashpw(data['password'].strip().encode('utf-8'), salt).decode('utf-8')

    if user:
        user['nombre'] = nombre
        user['email'] = email
        user['plan_id'] = plan_id
        user['limite_facturas_mes'] = limite
        user['max_clientes'] = max_cli
        user['activo'] = activo
        if new_pw_hash:
            user['password_hash'] = new_pw_hash
        _save_users(users)

    cuenta_dict = {
        'username': username,
        'nombre': nombre,
        'email': email,
        'telefono': telefono,
        'plan_id': plan_id,
        'limite_facturas_mes': limite,
        'creditos_extra': creditos,
        'max_clientes': max_cli,
        'activo': activo,
        'role': (user or {}).get('role', 'contador')
    }
    if new_pw_hash:
        cuenta_dict['password_hash'] = new_pw_hash

    with database.get_db() as conn:
        database.upsert_cuenta(conn, cuenta_dict)

    print(f"[ADMIN] Contador '{username}' actualizado.")
    return success(cuenta_dict, message="Contador actualizado correctamente")


@admin_bp.route('/api/admin/contadores/<username>', methods=['DELETE'])
@jwt_required(optional=True)
@handle_errors
def delete_contador(username):
    """DELETE /api/admin/contadores/<username> - Elimina o desactiva la cuenta."""
    if username in ('facturami1', 'admin'):
        return error('No se puede eliminar la cuenta principal de administrador', 400)

    users = _load_users()
    users = [u for u in users if u['username'] != username]
    _save_users(users)

    with database.get_db() as conn:
        database.delete_cuenta(conn, username)

    print(f"[ADMIN] Cuenta de contador eliminada: {username}")
    return success(message=f"Cuenta '{username}' eliminada")


@admin_bp.route('/api/admin/contadores/<username>/reset-facturas', methods=['POST'])
@jwt_required()
@handle_errors
def reset_facturas_contador(username):
    """POST /api/admin/contadores/<username>/reset-facturas - Reinicia a 0 el consumo mensual.
    Solo SuperAdmin. No elimina facturas; solo reinicia el conteo desde este momento."""
    if get_jwt().get('role') != 'admin':
        return error('Solo el administrador puede reiniciar contadores', 403)

    with database.get_db() as conn:
        if not database.reset_contador_facturas(conn, username):
            return error(f"Contador '{username}' no encontrado", 404)
        cuenta = database.get_cuenta(conn, username)

    print(f"[ADMIN] Contador de facturas reiniciado para '{username}' por {get_jwt_identity()}")
    return success({
        'username': username,
        'facturas_mes': cuenta.get('facturas_mes', 0),
        'contador_reset_at': cuenta.get('contador_reset_at', '')
    }, message=f"Contador de facturas de '{username}' reiniciado")
