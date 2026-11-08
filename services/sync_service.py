"""
FACTURAMI - Servicio de Sincronizacion.
Logica de negocio para pull/push de datos entre frontend y SQLite / usb_database.json.
"""
import os, json
import db as database

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
USB_JSON = os.environ.get('FACTURAMI_USB_JSON') or os.path.join(BASE_DIR, 'usb_database.json')

def pull(cuenta_id=None):
    """
    Genera el payload completo compatible con el frontend, filtrando por cuenta_id si corresponde.
    Retorna (data, is_empty).
    """
    if not cuenta_id:
        return {'f_clients': []}, True

    with database.get_db() as conn:
        data = database.sync_pull(conn, cuenta_id=cuenta_id)
    is_empty = len(data.get('f_clients', [])) == 0
    return data, is_empty


def push(data, cuenta_id=None):
    """Persiste los datos del frontend en SQLite y en usb_database.json."""
    c_owner = cuenta_id or data.get('cuenta_id') or 'facturami1'
    with database.get_db() as conn:
        database.sync_push(conn, data, cuenta_id=c_owner)
    try:
        with open(USB_JSON, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[SYNC] Error guardando usb_database.json: {e}")
    print(f"[SYNC] Datos sincronizados a SQLite para cuenta: {c_owner}.")
