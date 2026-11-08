"""
FACTURAMI - Rutas de Autenticacion.
Blueprint: /api/login, /api/auth/*
"""
import time
from collections import defaultdict
from flask import Blueprint, request
from flask_jwt_extended import jwt_required, create_access_token, get_jwt_identity

from errors import success, error, handle_errors, validate, ValidationError
from schemas import LOGIN_SCHEMA
from services.auth_service import authenticate
from services.authz import auth_required

auth_bp = Blueprint('auth', __name__)

# ── Protección contra Fuerza Bruta en Login ───────────────────────────
_FAILED_LOGINS = defaultdict(list)
_MAX_FAILURES = 5
_LOCKOUT_SECONDS = 900  # 15 minutos

def _get_client_ip():
    """Obtiene la IP real del cliente considerando proxies (Caddy, Cloudflare)."""
    return request.headers.get('X-Forwarded-For', request.headers.get('X-Real-IP', request.remote_addr or '127.0.0.1')).split(',')[0].strip()


@auth_bp.route('/api/login', methods=['POST'])
@handle_errors
def login():
    """POST /api/login - Autentica usuario y devuelve token JWT con proteccion contra fuerza bruta."""
    ip = _get_client_ip()
    now = time.time()

    # Limpiar intentos viejos
    _FAILED_LOGINS[ip] = [t for t in _FAILED_LOGINS[ip] if now - t < _LOCKOUT_SECONDS]

    if len(_FAILED_LOGINS[ip]) >= _MAX_FAILURES:
        tiempo_restante = int((_LOCKOUT_SECONDS - (now - _FAILED_LOGINS[ip][0])) / 60) + 1
        return error(f'Demasiados intentos fallidos. IP bloqueada temporalmente por {tiempo_restante} minutos.', 429)

    data = request.get_json(force=True, silent=True)
    if not data:
        return error('Se requiere un body JSON', 400)

    try:
        cleaned = validate(data, LOGIN_SCHEMA)
    except ValidationError as e:
        return error(e.message, 400)

    user = authenticate(cleaned['username'], cleaned['password'])
    if not user:
        _FAILED_LOGINS[ip].append(now)
        intentos_restantes = _MAX_FAILURES - len(_FAILED_LOGINS[ip])
        if intentos_restantes > 0:
            return error(f'Credenciales invalidas. Quedan {intentos_restantes} intento(s) antes del bloqueo.', 401)
        else:
            return error('Credenciales invalidas. Has alcanzado el limite de intentos. Espera 15 minutos.', 429)

    # Login exitoso: limpiar historial de intentos fallidos
    _FAILED_LOGINS.pop(ip, None)

    if isinstance(user, dict):
        if user.get('error') == 'CUENTA_SUSPENDIDA':
            return error('Tu cuenta ha sido pausada o suspendida. Por favor contacta al administrador.', 403)
        if user.get('error') == 'SUSCRIPCION_VENCIDA':
            fv = user.get('fecha_vencimiento', '')
            msg = f'Tu suscripción ha vencido{(" el " + fv) if fv else ""}. Por favor contacta al administrador para renovar o habilitar tu acceso.'
            return error(msg, 403)

    canonical_username = str(user['username']).strip().lower()
    token = create_access_token(
        identity=canonical_username,
        additional_claims={'role': user['role']}
    )

    print(f"[AUTH] Login exitoso: {canonical_username} ({user['role']})")
    return success({
        'token': token,
        'user': user
    })


@auth_bp.route('/api/auth/verify', methods=['GET'])
@auth_required
@handle_errors
def verify_token():
    """GET /api/auth/verify - Verifica que un token JWT siga valido y la cuenta siga activa."""
    current_user = get_jwt_identity()
    return success({'user': current_user})
