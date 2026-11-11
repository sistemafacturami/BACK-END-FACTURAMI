"""
FACTURAMI - Rutas de Administracion de Contadores y Suscripciones (SuperAdmin).
Blueprint: /api/admin/*
"""
import os
import json
import re
import time
from datetime import datetime, date, timedelta
import bcrypt
from flask import Blueprint, request
from flask_jwt_extended import get_jwt_identity, get_jwt

from services.authz import role_required, CLIENT_ID_RE

USERNAME_RE = re.compile(r'^[a-z0-9._-]{3,32}$')

import db as database
from errors import success, error, handle_errors

admin_bp = Blueprint('admin', __name__)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
USERS_FILE = os.environ.get('FACTURAMI_USERS_FILE') or os.path.join(BASE_DIR, 'users.json')


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
@role_required('admin')
@handle_errors
def list_contadores():
    """GET /api/admin/contadores - Lista todas las cuentas de contadores con su consumo actual."""
    with database.get_db() as conn:
        cuentas = database.get_cuentas(conn)
    return success(cuentas)


@admin_bp.route('/api/admin/contadores', methods=['POST'])
@role_required('admin')
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

    if not USERNAME_RE.fullmatch(username):
        return error('El nombre de usuario debe tener entre 3 y 32 caracteres (solo minúsculas, números, puntos, guiones y guiones bajos)', 400)

    users = _load_users()
    if any(u['username'].lower() == username for u in users):
        return error(f"El usuario '{username}' ya existe", 409)

    # Evitar que una nueva cuenta adopte clientes huérfanos preexistentes
    with database.get_db() as conn:
        cuenta_exist = database.get_cuenta(conn, username)
        if cuenta_exist:
            return error(f"La cuenta '{username}' ya existe en la base de datos", 409)
        orphans = conn.execute(
            "SELECT COUNT(*) FROM clientes WHERE LOWER(TRIM(cuenta_id)) = LOWER(TRIM(?))",
            (username,)
        ).fetchone()[0]
        if orphans > 0:
            return error(f"No se puede crear la cuenta '{username}': existen {orphans} cliente(s) registrados con este cuenta_id. Transfiéralos primero.", 409)

    # Determinar limites segun el plan
    plan_meta = PLANES_DEF.get(plan_id, PLANES_DEF['despacho'])
    limite = int(data.get('limite_facturas_mes') or plan_meta['facturas'])
    max_cli = int(data.get('max_clientes') or plan_meta['max_clientes'])

    # Suscripcion inicial: por defecto 30 dias (1 mes)
    from datetime import date, timedelta
    dias_ini = int(data.get('dias_suscripcion') or 30)
    suscripcion_hasta = data.get('suscripcion_hasta') or (date.today() + timedelta(days=dias_ini)).strftime('%Y-%m-%d')

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
        'suscripcion_hasta': suscripcion_hasta,
        'activo': 1
    })
    _save_users(users)

    # Guardar en base de datos SQLite
    admin_username = get_jwt_identity()
    cuenta_dict = {
        'username': username,
        'nombre': nombre or username,
        'email': email,
        'telefono': telefono,
        'plan_id': plan_id,
        'limite_facturas_mes': limite,
        'creditos_extra': 0,
        'max_clientes': max_cli,
        'suscripcion_hasta': suscripcion_hasta,
        'activo': 1,
        'role': 'contador',
        'password_hash': pw_hash
    }
    with database.get_db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        database.upsert_cuenta(conn, cuenta_dict)
        database.insert_audit_log(
            conn,
            accion='crear_cuenta',
            actor=admin_username,
            target_tipo='cuenta',
            target_id=username,
            detalles={'plan_id': plan_id, 'nombre': nombre or username, 'email': email, 'max_clientes': max_cli}
        )

    print(f"[ADMIN] Contador creado exitosamente: {username} ({plan_id})")
    return success(cuenta_dict, message=f"Contador '{nombre or username}' creado exitosamente", status=201)


@admin_bp.route('/api/admin/contadores/<username>', methods=['PUT'])
@role_required('admin')
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
        if 'suscripcion_hasta' in data:
            user['suscripcion_hasta'] = str(data['suscripcion_hasta']).strip()
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
    if 'suscripcion_hasta' in data:
        cuenta_dict['suscripcion_hasta'] = str(data['suscripcion_hasta']).strip()
    if new_pw_hash:
        cuenta_dict['password_hash'] = new_pw_hash

    admin_username = get_jwt_identity()
    with database.get_db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        database.upsert_cuenta(conn, cuenta_dict)

        # Bitácora de auditoría detallada
        if 'activo' in data and (cuenta is None or cuenta.get('activo') != activo):
            database.insert_audit_log(
                conn,
                accion='suspender_cuenta' if activo == 0 else 'reactivar_cuenta',
                actor=admin_username,
                target_tipo='cuenta',
                target_id=username,
                detalles={'activo': activo}
            )
        if new_pw_hash:
            database.insert_audit_log(
                conn,
                accion='cambio_password',
                actor=admin_username,
                target_tipo='cuenta',
                target_id=username,
                detalles={'motivo': 'admin_reset'}
            )
        database.insert_audit_log(
            conn,
            accion='actualizar_cuenta',
            actor=admin_username,
            target_tipo='cuenta',
            target_id=username,
            detalles={'plan_id': plan_id, 'activo': activo, 'limite': limite, 'max_clientes': max_cli}
        )

    print(f"[ADMIN] Contador '{username}' actualizado.")
    return success(cuenta_dict, message="Contador actualizado correctamente")


@admin_bp.route('/api/admin/contadores/<username>/extender-suscripcion', methods=['POST'])
@role_required('admin')
@handle_errors
def extender_suscripcion_contador(username):
    """POST /api/admin/contadores/<username>/extender-suscripcion - Habilita o renueva la suscripcion."""
    if get_jwt().get('role') != 'admin':
        return error('Solo el administrador puede renovar o extender suscripciones', 403)

    data = request.get_json(force=True, silent=True) or {}
    dias = data.get('dias')
    meses = data.get('meses')
    fecha_hasta = data.get('fecha_hasta')

    if meses:
        dias = int(meses) * 30
    elif dias:
        dias = int(dias)
    elif not fecha_hasta:
        dias = 30  # Por defecto +30 dias (+1 mes)

    admin_username = get_jwt_identity()
    with database.get_db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        nueva_fecha = database.extender_suscripcion(conn, username, dias=dias, fecha_fija=fecha_hasta)
        if not nueva_fecha:
            return error(f"Contador '{username}' no encontrado", 404)
        cuenta = database.get_cuenta(conn, username)
        database.insert_audit_log(
            conn,
            accion='extender_suscripcion',
            actor=admin_username,
            target_tipo='cuenta',
            target_id=username,
            detalles={'nueva_fecha': nueva_fecha, 'dias': dias, 'fecha_hasta': fecha_hasta}
        )

    # Mantener sincronizado users.json
    users = _load_users()
    for u in users:
        if u.get('username', '').lower() == username.lower():
            u['suscripcion_hasta'] = nueva_fecha
            u['activo'] = 1
            break
    _save_users(users)

    print(f"[ADMIN] Suscripción de '{username}' extendida hasta {nueva_fecha}")
    return success(cuenta, message=f"Suscripción de @{username} habilitada hasta {nueva_fecha}")


@admin_bp.route('/api/admin/contadores/<username>', methods=['DELETE'])
@role_required('admin')
@handle_errors
def delete_contador(username):
    """DELETE /api/admin/contadores/<username> - Elimina o desactiva la cuenta si no tiene clientes."""
    u_clean = (username or '').strip().lower()
    if u_clean in ('facturami1', 'admin'):
        return error('No se puede eliminar la cuenta principal de administrador', 400)

    admin_username = get_jwt_identity()
    with database.get_db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        n = conn.execute(
            "SELECT COUNT(*) FROM clientes WHERE LOWER(TRIM(cuenta_id))=LOWER(TRIM(?))",
            (u_clean,)
        ).fetchone()[0]
        if n > 0:
            return error(f'Tiene {n} clientes; transfiérelos primero', 409)

        database.delete_cuenta(conn, u_clean)
        database.insert_audit_log(
            conn,
            accion='eliminar_cuenta',
            actor=admin_username,
            target_tipo='cuenta',
            target_id=u_clean
        )

    users = _load_users()
    users = [u for u in users if u['username'].lower() != u_clean]
    _save_users(users)

    print(f"[ADMIN] Cuenta de contador eliminada: {u_clean}")
    return success(message=f"Cuenta '{u_clean}' eliminada")


@admin_bp.route('/api/admin/contadores/<username>/reset-facturas', methods=['POST'])
@role_required('admin')
@handle_errors
def reset_facturas_contador(username):
    """POST /api/admin/contadores/<username>/reset-facturas - Reinicia a 0 el consumo mensual.
    Solo SuperAdmin. No elimina facturas; solo reinicia el conteo desde este momento."""
    if get_jwt().get('role') != 'admin':
        return error('Solo el administrador puede reiniciar contadores', 403)

    admin_username = get_jwt_identity()
    with database.get_db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        if not database.reset_contador_facturas(conn, username):
            return error(f"Contador '{username}' no encontrado", 404)
        cuenta = database.get_cuenta(conn, username)
        database.insert_audit_log(
            conn,
            accion='reset_facturas',
            actor=admin_username,
            target_tipo='cuenta',
            target_id=username,
            detalles={'facturas_mes': cuenta.get('facturas_mes', 0), 'reset_at': cuenta.get('contador_reset_at', '')}
        )

    print(f"[ADMIN] Contador de facturas reiniciado para '{username}' por {get_jwt_identity()}")
    return success({
        'username': username,
        'facturas_mes': cuenta.get('facturas_mes', 0),
        'contador_reset_at': cuenta.get('contador_reset_at', '')
    }, message=f"Contador de facturas de '{username}' reiniciado")


@admin_bp.route('/api/admin/transferir-cliente', methods=['POST'])
@role_required('admin')
@handle_errors
def transfer_cliente():
    """POST /api/admin/transferir-cliente - Reasigna un cliente a otro contador (solo superadmin)."""
    body = request.get_json(force=True, silent=True) or {}
    cliente_id = body.get('cliente_id')
    nuevo_cuenta_id = body.get('nuevo_cuenta_id')
    if not cliente_id or not nuevo_cuenta_id:
        return error("Se requiere cliente_id y nuevo_cuenta_id", 400)

    cid_clean = str(cliente_id).strip()
    if not CLIENT_ID_RE.fullmatch(cid_clean):
        return error(f"ID de cliente invalido: {cid_clean}", 400)

    nuevo_clean = str(nuevo_cuenta_id).strip().lower()
    admin_username = get_jwt_identity()

    with database.get_db() as conn:
        # Serializar lectura y escritura de forma atómica
        conn.execute("BEGIN IMMEDIATE")

        cli = conn.execute("SELECT id, cuenta_id, nombre FROM clientes WHERE id=?", (cid_clean,)).fetchone()
        if not cli:
            return error(f"Cliente {cid_clean} no encontrado", 404)

        if (cli['cuenta_id'] or '').strip().lower() == nuevo_clean:
            return error(f"El cliente ya pertenece a la cuenta '{nuevo_clean}'", 400)

        cuenta = conn.execute(
            "SELECT username, activo FROM cuentas WHERE LOWER(TRIM(username))=LOWER(TRIM(?))",
            (nuevo_clean,)
        ).fetchone()
        if not cuenta:
            return error(f"Cuenta de destino '{nuevo_clean}' no existe", 404)

        # Tratar activo NULL como 1 (consistente con _cuenta_activa)
        activo_val = cuenta['activo'] if cuenta['activo'] is not None else 1
        if int(activo_val) != 1:
            return error(f"Cuenta de destino '{cuenta['username']}' no está activa", 400)

        conn.execute(
            "UPDATE clientes SET cuenta_id=?, updated_at=datetime('now','localtime') WHERE id=?",
            (cuenta['username'], cid_clean)
        )

        database.insert_audit_log(
            conn,
            accion='transferencia_cliente',
            actor=admin_username,
            target_tipo='cliente',
            target_id=cid_clean,
            detalles={
                'cliente_nombre': cli['nombre'],
                'cuenta_anterior': cli['cuenta_id'],
                'cuenta_nueva': cuenta['username']
            }
        )

    print(f"[AUDIT] Cliente '{cid_clean}' transferido de '{cli['cuenta_id']}' a '{cuenta['username']}' por admin '{admin_username}'")
    return success(message=f"Cliente {cid_clean} transferido exitosamente a cuenta activa {cuenta['username']}")
