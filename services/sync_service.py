"""
FACTURAMI - Servicio de Sincronizacion.
Logica de negocio para pull/push de datos entre frontend y SQLite.
Multi-tenant: No comparte archivos planos (usb_database.json) entre cuentas.
"""
import db as database

def pull(cuenta_id):
    """
    Genera el payload completo compatible con el frontend, filtrando por cuenta_id.
    Retorna (data, is_empty). Lanza ValueError si falta cuenta_id.
    """
    if not cuenta_id or not str(cuenta_id).strip():
        raise ValueError("cuenta_id requerido")

    with database.get_db() as conn:
        data = database.sync_pull(conn, cuenta_id=cuenta_id)
    is_empty = len(data.get('f_clients', [])) == 0
    return data, is_empty


def push(data, cuenta_id=None):
    """Persiste los datos del frontend en SQLite de forma atómica y aislada por cuenta."""
    c_owner = cuenta_id or data.get('cuenta_id')
    if not c_owner:
        raise ValueError("cuenta_id es requerido para sincronizar")
    with database.get_db() as conn:
        database.sync_push(conn, data, cuenta_id=c_owner)
    print(f"[SYNC] Datos sincronizados a SQLite para cuenta: {c_owner}.")
