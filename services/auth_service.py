"""
FACTURAMI - Servicio de Autenticacion.
Logica de negocio para login, verificacion de usuarios y tokens.
"""
import os
import json
import bcrypt

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
USERS_FILE = os.environ.get('FACTURAMI_USERS_FILE') or os.path.join(BASE_DIR, 'users.json')


def _load_users():
    """Carga usuarios desde users.json."""
    if not os.path.exists(USERS_FILE):
        return []
    with open(USERS_FILE, 'r', encoding='utf-8') as f:
        return json.load(f)


def authenticate(username, password):
    """
    Valida credenciales del usuario de forma flexible y segura:
    - Insensible a mayusculas/minusculas (e.g. FYM == fym == Fym)
    - Soporta inicio de sesion por usuario o por correo electronico
    - Valida contra users.json Y contra la base de datos SQLite (tabla cuentas)
    """
    if not username or not password:
        return None

    u_clean = username.strip().lower()
    p_clean = password.strip()

    # 1. Buscar en users.json
    users = _load_users()
    user = next((
        u for u in users 
        if (u.get('username') or '').strip().lower() == u_clean 
        or (u.get('email') and u.get('email').strip().lower() == u_clean)
        or (u.get('nombre') and u.get('nombre').strip().lower() == u_clean)
    ), None)

    # 2. Buscar en SQLite cuentas
    cuenta_db = None
    try:
        import db as database
        with database.get_db() as conn:
            cur = conn.execute(
                "SELECT * FROM cuentas WHERE LOWER(TRIM(username)) = ? OR (email != '' AND LOWER(TRIM(email)) = ?) OR (nombre != '' AND LOWER(TRIM(nombre)) = ?)",
                (u_clean, u_clean, u_clean)
            )
            row = cur.fetchone()
            if row:
                cuenta_db = dict(row)
    except Exception as e:
        print(f"[AUTH] Error consultando SQLite cuentas: {e}")

    # Obtener el hash de contraseña (prioridad a base de datos SQLite)
    pw_hash = (cuenta_db or {}).get('password_hash') or (user or {}).get('password_hash')

    if not pw_hash:
        print(f"[AUTH] Usuario '{username}' no encontrado ni en users.json ni en cuentas SQLite.")
        return None

    # Validar password con bcrypt
    try:
        if not bcrypt.checkpw(p_clean.encode('utf-8'), pw_hash.encode('utf-8')):
            print(f"[AUTH] Contraseña incorrecta para usuario '{username}'")
            return None
    except Exception as e:
        print(f"[AUTH] Error al verificar hash bcrypt para '{username}': {e}")
        return None

    # Extraer perfil y permisos finales combinados
    final_username = (user or {}).get('username') or (cuenta_db or {}).get('username') or u_clean
    role = (cuenta_db or {}).get('role') or (user or {}).get('role', 'contador')
    nombre = (cuenta_db or {}).get('nombre') or (user or {}).get('nombre', final_username)
    plan_id = (cuenta_db or {}).get('plan_id') or (user or {}).get('plan_id', 'master' if role == 'admin' else 'despacho')
    limite = (cuenta_db or {}).get('limite_facturas_mes') or (user or {}).get('limite_facturas_mes', 999999 if role == 'admin' else 3000)
    max_cli = (cuenta_db or {}).get('max_clientes') or (user or {}).get('max_clientes', 999999 if role == 'admin' else 9999)
    raw_activo = (cuenta_db or {}).get('activo') if cuenta_db and 'activo' in cuenta_db else (user or {}).get('activo', 1)
    activo = 1 if raw_activo is None else int(raw_activo)

    if activo == 0:
        return {'error': 'CUENTA_SUSPENDIDA', 'username': final_username}

    suscripcion_hasta = (cuenta_db or {}).get('suscripcion_hasta') or (user or {}).get('suscripcion_hasta') or ''
    if role != 'admin' and suscripcion_hasta:
        try:
            from datetime import datetime, date
            exp_date = datetime.strptime(str(suscripcion_hasta)[:10], '%Y-%m-%d').date()
            if date.today() > exp_date:
                return {
                    'error': 'SUSCRIPCION_VENCIDA',
                    'username': final_username,
                    'fecha_vencimiento': exp_date.strftime('%d/%m/%Y')
                }
        except Exception as e:
            print(f"[AUTH] Error comprobando suscripcion_hasta '{suscripcion_hasta}': {e}")

    facturas_mes = (cuenta_db or {}).get('facturas_mes', 0)

    return {
        'username': final_username,
        'role': role,
        'nombre': nombre,
        'plan_id': plan_id,
        'limite_facturas_mes': limite,
        'facturas_mes': facturas_mes,
        'max_clientes': max_cli,
        'suscripcion_hasta': suscripcion_hasta,
        'activo': activo
    }


def validate_n8n_token(req):
    """
    Valida el token interno de n8n (header X-N8N-Token) contra N8N_INTERNAL_SECRET.

    - Sin valor por defecto: si la variable no esta definida, se rechaza todo.
    - Comparacion en tiempo constante (hmac.compare_digest).
    - NO se acepta por IP de origen: detras de Caddy remote_addr siempre es la
      IP privada del proxy, asi que ese criterio no filtraria nada.
    """
    import hmac
    expected = os.environ.get('N8N_INTERNAL_SECRET', '')
    if not expected:
        return False
    got = req.headers.get('X-N8N-Token', '')
    return hmac.compare_digest(got.encode('utf-8'), expected.encode('utf-8'))
