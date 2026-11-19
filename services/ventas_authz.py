"""
FACTURAMI - Roles y permisos del Sistema de Ventas por empresa (Fase 3).
=======================================================================
Rol efectivo del usuario autenticado sobre una empresa (clientes.id):
  - Dueño de la cuenta (clientes.cuenta_id == identidad del token)  -> admin_empresa
  - Usuario interno registrado en company_users (activo)           -> su rol
  - Cualquier otro (incluido el admin global de FACTURAMI)         -> sin acceso al contenido
Las respuestas de "sin acceso" son siempre 403 genérico (no revelan si la empresa existe).
"""
from flask import jsonify
from flask_jwt_extended import get_jwt_identity

import db as database
from services.authz import valid_client_id

ROLES = ('admin_empresa', 'contador', 'vendedor', 'cajero')

ALL_PERMISSIONS = {
    'dashboard.view', 'docs.view', 'reports.view', 'customers.view', 'customers.manage',
    'products.view', 'products.manage', 'sales.create', 'cash.manage',
    'config.view', 'config.write', 'config.secrets', 'config.contable', 'users.manage',
}

ROLE_PERMISSIONS = {
    'admin_empresa': set(ALL_PERMISSIONS),
    'contador': {'dashboard.view', 'docs.view', 'reports.view', 'customers.view',
                 'products.view', 'config.view', 'config.contable'},
    'vendedor': {'dashboard.view', 'docs.view', 'sales.create', 'customers.view',
                 'customers.manage', 'products.view'},
    'cajero': {'dashboard.view', 'docs.view', 'sales.create', 'cash.manage', 'products.view', 'customers.view'},
}


def _deny(status=403, message='Acceso denegado a la empresa'):
    return jsonify({'ok': False, 'error': message}), status


def company_role(company_id: str, username: str = None):
    """Devuelve el rol efectivo o None. `username` por defecto es la identidad del token."""
    if not valid_client_id(company_id):
        return None
    user = str(username if username is not None else (get_jwt_identity() or '')).strip().lower()
    if not user:
        return None
    with database.get_db() as conn:
        owner = conn.execute(
            "SELECT 1 FROM clientes WHERE id = ? AND LOWER(TRIM(cuenta_id)) = ?",
            (str(company_id), user)
        ).fetchone()
        if owner:
            return 'admin_empresa'
        row = conn.execute(
            "SELECT role FROM company_users WHERE company_id = ? AND LOWER(username) = ? AND activo = 1",
            (str(company_id), user)
        ).fetchone()
        return row['role'] if row else None


def permissions_for(role):
    return sorted(ROLE_PERMISSIONS.get(role, set()))


def require_company_permission(company_id: str, permission: str):
    """Devuelve (rol, None) si autorizado o (None, respuesta_error)."""
    if not valid_client_id(company_id):
        return None, _deny(400, 'Empresa invalida')
    role = company_role(company_id)
    if role is None or permission not in ROLE_PERMISSIONS.get(role, set()):
        return None, _deny()
    return role, None
