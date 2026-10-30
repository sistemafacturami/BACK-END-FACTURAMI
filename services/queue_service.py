"""
FACTURAMI - Servicio de Cola de Facturas.
Logica de negocio para la cola de procesamiento de n8n.
"""
import db as database


def get_pendientes(client_id):
    """Obtiene todas las facturas pendientes de un cliente."""
    with database.get_db() as conn:
        return database.queue_get_pendientes(conn, client_id)


def count_pendientes(client_id):
    """Cuenta facturas pendientes de un cliente."""
    with database.get_db() as conn:
        return database.queue_count_pendientes(conn, client_id)


def agregar(data):
    """
    Agrega una factura a la cola con deduplicacion.
    Retorna (ok, result) donde result es el ID o un mensaje de error.
    """
    raw_cid = str(data.get('client_id') or data.get('CLIENTE_ID') or '').strip()
    num = data.get('FACTURA') or data.get('NUMEROFACTURA') or ''
    prov = data.get('PROVEEDOR') or data.get('proveedor') or ''
    h_archivo = data.get('hash_archivo') or ''

    with database.get_db() as conn:
        if not raw_cid or raw_cid == 'default':
            email = data.get('email') or data.get('to') or ''
            cfg = database.resolve_client_by_email(conn, email)
            client_id = cfg['cliente_id'] if cfg else '504470991'
            data['client_id'] = client_id
        else:
            client_id = raw_cid

        # Deduplicar
        if database.queue_check_duplicado(conn, client_id, num, proveedor=prov, hash_archivo=h_archivo):
            return False, f'Factura {num} ya esta en la cola'

        q_id = database.queue_add(conn, data)

    print(f"[QUEUE] Nueva factura [{data.get('origen','pdf_manual')}] para {client_id}: {prov} | {num} | {data.get('TOTAL','')}")
    return True, q_id


def absorber(factura_id):
    """Marca una factura como absorbida."""
    with database.get_db() as conn:
        database.queue_absorber(conn, factura_id)


def eliminar(factura_id):
    """Elimina una factura de la cola."""
    with database.get_db() as conn:
        database.queue_eliminar(conn, factura_id)


def get_by_id(factura_id):
    """Obtiene una factura de la cola por su ID."""
    with database.get_db() as conn:
        return database.queue_get_by_id(conn, factura_id)
