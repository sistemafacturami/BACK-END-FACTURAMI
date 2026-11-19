"""
FACTURAMI - Administración Comercial Multiempresa (Fase 4).
============================================================
Gestión completa, validada y aislada de:
  - Clientes comerciales (compradores / receptores) con validación estricta de cédula CR y control de crédito.
  - Categorías de productos y servicios por empresa.
  - Catálogo de productos y servicios con códigos CABYS (13 dígitos), tarifas de IVA oficiales de Hacienda,
    márgenes de utilidad, listas de precios (PVP, mayorista, especial) y control de stock mínimo.
  - Unidades de medida oficiales del Ministerio de Hacienda.
Cada acción queda registrada en audit_log dentro de la misma transacción.
"""
import re
import uuid
from datetime import datetime

import db as database

# ── Validaciones y constantes ──────────────────────────────────────────

TIPO_IDENTIFICACION_MAP = {
    '01': 'Cédula Física (9 dígitos)',
    '02': 'Cédula Jurídica (10 dígitos)',
    '03': 'DIMEX (11-12 dígitos)',
    '04': 'NITE (10 dígitos)',
    '05': 'Extranjero / Pasaporte',
}

CONDICIONES_VENTA = {
    '01': 'Efectivo',
    '02': 'Tarjeta',
    '03': 'Cheque',
    '04': 'Transferencia / SINPE',
    '05': 'Recaudado por terceros',
    '06': 'Crédito',
    '99': 'Otros',
}

UNIDADES_MEDIDA = [
    {'codigo': 'Unid', 'nombre': 'Unidad / Servicios'},
    {'codigo': 'kg', 'nombre': 'Kilogramo'},
    {'codigo': 'g', 'nombre': 'Gramo'},
    {'codigo': 'm', 'nombre': 'Metro'},
    {'codigo': 'cm', 'nombre': 'Centímetro'},
    {'codigo': 'km', 'nombre': 'Kilómetro'},
    {'codigo': 'l', 'nombre': 'Litro'},
    {'codigo': 'ml', 'nombre': 'Mililitro'},
    {'codigo': 'h', 'nombre': 'Hora'},
    {'codigo': 's', 'nombre': 'Segundo'},
    {'codigo': 'Sp', 'nombre': 'Servicios Profesionales'},
    {'codigo': 'paq', 'nombre': 'Paquete'},
    {'codigo': 'caj', 'nombre': 'Caja'},
    {'codigo': 'set', 'nombre': 'Set / Juego'},
    {'codigo': 'lote', 'nombre': 'Lote'},
]

DIGITS_ONLY = re.compile(r'^\d+$')
EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')
CABYS_RE = re.compile(r'^\d{13}$')


class ComercialError(ValueError):
    pass


def clean_identificacion(tipo_id: str, numero: str) -> str:
    """Limpia y valida el número de identificación según las reglas oficiales de Costa Rica."""
    tipo = str(tipo_id or '01').strip()
    if tipo not in TIPO_IDENTIFICACION_MAP:
        raise ComercialError(f"Tipo de identificación inválido: '{tipo}'")

    raw = re.sub(r'[\s\-_.]', '', str(numero or ''))
    if not raw:
        raise ComercialError("El número de identificación es requerido")

    if tipo == '01':
        if not DIGITS_ONLY.match(raw) or len(raw) != 9:
            raise ComercialError("Cédula física debe contener exactamente 9 dígitos numéricos")
    elif tipo == '02':
        if not DIGITS_ONLY.match(raw) or len(raw) != 10:
            raise ComercialError("Cédula jurídica debe contener exactamente 10 dígitos numéricos")
    elif tipo == '03':
        if not DIGITS_ONLY.match(raw) or len(raw) not in (11, 12):
            raise ComercialError("DIMEX debe contener 11 o 12 dígitos numéricos")
    elif tipo == '04':
        if not DIGITS_ONLY.match(raw) or len(raw) != 10:
            raise ComercialError("NITE debe contener exactamente 10 dígitos numéricos")
    elif tipo == '05':
        if len(raw) < 2 or len(raw) > 20:
            raise ComercialError("Identificación de extranjero debe tener entre 2 y 20 caracteres")
    return raw


def clean_cabys(cabys: str) -> str:
    """Valida que el código CABYS sea exactamente de 13 dígitos numéricos."""
    c = re.sub(r'[\s\-.]', '', str(cabys or ''))
    if not CABYS_RE.match(c):
        raise ComercialError("Código CABYS debe contener exactamente 13 dígitos numéricos")
    return c


# ── Clientes Comerciales ──────────────────────────────────────────────

def list_customers(company_id: str, q: str = None, estado: str = 'activo', limit: int = 100, offset: int = 0):
    cid = str(company_id or '').strip()
    limit = max(1, min(int(limit or 100), 500))
    offset = max(0, int(offset or 0))

    where = ["company_id = ?"]
    params = [cid]

    if estado and estado != 'todos':
        where.append("estado = ?")
        params.append(estado)

    if q and q.strip():
        term = f"%{q.strip()}%"
        where.append("(nombre LIKE ? OR nombre_comercial LIKE ? OR identificacion LIKE ? OR email LIKE ? OR telefono LIKE ?)")
        params.extend([term, term, term, term, term])

    where_sql = " AND ".join(where)

    with database.get_db() as conn:
        total = conn.execute(f"SELECT COUNT(*) FROM sales_customers WHERE {where_sql}", params).fetchone()[0]

        query = f"""
            SELECT c.*,
                   COALESCE((
                       SELECT SUM(ar.saldo_pendiente)
                       FROM accounts_receivable ar
                       WHERE ar.company_id = c.company_id
                         AND ar.customer_id = c.id
                         AND ar.estado IN ('pendiente', 'parcial', 'vencida')
                   ), 0.0) AS saldo_pendiente,
                   COALESCE((
                       SELECT COUNT(*)
                       FROM electronic_documents ed
                       WHERE ed.company_id = c.company_id
                         AND ed.customer_id = c.id
                   ), 0) AS facturas_emitidas_count
            FROM sales_customers c
            WHERE {where_sql}
            ORDER BY c.nombre COLLATE NOCASE ASC
            LIMIT ? OFFSET ?
        """
        rows = conn.execute(query, params + [limit, offset]).fetchall()

        items = []
        for r in rows:
            d = dict(r)
            saldo = float(d.get('saldo_pendiente') or 0.0)
            limite = float(d.get('limite_credito') or 0.0)
            d['credito_disponible'] = max(0.0, round(limite - saldo, 2))
            items.append(d)

        return {'items': items, 'total': total, 'limit': limit, 'offset': offset}


def get_customer(company_id: str, customer_id: str):
    cid = str(company_id or '').strip()
    cust_id = str(customer_id or '').strip()

    with database.get_db() as conn:
        r = conn.execute(
            "SELECT * FROM sales_customers WHERE id = ? AND company_id = ?",
            (cust_id, cid)
        ).fetchone()
        if not r:
            raise ComercialError("Cliente comercial no encontrado")

        d = dict(r)

        # Resumen financiero
        ar_summary = conn.execute("""
            SELECT
                COALESCE(SUM(monto_original), 0.0) AS total_facturado_credito,
                COALESCE(SUM(monto_abonado), 0.0) AS total_abonado,
                COALESCE(SUM(saldo_pendiente), 0.0) AS saldo_pendiente,
                COUNT(CASE WHEN estado = 'vencida' THEN 1 END) AS facturas_vencidas_count
            FROM accounts_receivable
            WHERE company_id = ? AND customer_id = ? AND estado != 'anulada'
        """, (cid, cust_id)).fetchone()

        saldo = float(ar_summary['saldo_pendiente'] or 0.0)
        limite = float(d.get('limite_credito') or 0.0)

        d['saldo_pendiente'] = saldo
        d['total_facturado_credito'] = float(ar_summary['total_facturado_credito'] or 0.0)
        d['total_abonado'] = float(ar_summary['total_abonado'] or 0.0)
        d['facturas_vencidas_count'] = int(ar_summary['facturas_vencidas_count'] or 0)
        d['credito_disponible'] = max(0.0, round(limite - saldo, 2))

        # Últimos 10 comprobantes emitidos
        docs = conn.execute("""
            SELECT id, tipo_documento, consecutivo, fecha_emision, total_comprobante, moneda, estado_comprobante
            FROM electronic_documents
            WHERE company_id = ? AND customer_id = ?
            ORDER BY fecha_emision DESC, created_at DESC
            LIMIT 10
        """, (cid, cust_id)).fetchall()
        d['ultimos_documentos'] = [dict(x) for x in docs]

        return d


def create_customer(company_id: str, body: dict, actor: str):
    cid = str(company_id or '').strip()
    if not cid:
        raise ComercialError("company_id es requerido")

    tipo_id = str(body.get('tipo_identificacion') or '01').strip()
    identificacion = clean_identificacion(tipo_id, body.get('identificacion'))

    nombre = str(body.get('nombre') or '').strip()
    if not nombre:
        raise ComercialError("El nombre o razón social del cliente es requerido")
    if len(nombre) > 160:
        raise ComercialError("El nombre excede los 160 caracteres")

    nombre_comercial = str(body.get('nombre_comercial') or '').strip()[:160]

    email = str(body.get('email') or '').strip()
    if email:
        if len(email) > 120 or not EMAIL_RE.match(email):
            raise ComercialError("El correo electrónico del cliente no tiene formato válido")

    telefono = str(body.get('telefono') or '').strip()[:30]
    provincia = str(body.get('provincia') or '1').strip()[:2]
    canton = str(body.get('canton') or '01').strip()[:2]
    distrito = str(body.get('distrito') or '01').strip()[:2]
    barrio = str(body.get('barrio') or '').strip()[:80]
    otras_senas = str(body.get('otras_senas') or body.get('direccion') or '').strip()[:250]
    direccion = otras_senas
    actividad_economica = str(body.get('actividad_economica') or '').strip()[:20]

    cond_venta = str(body.get('condicion_venta') or '01').strip()
    if cond_venta not in CONDICIONES_VENTA:
        raise ComercialError(f"Condición de venta '{cond_venta}' inválida")

    try:
        limite_credito = max(0.0, float(body.get('limite_credito') or 0.0))
        plazo_credito = max(0, min(int(body.get('plazo_credito') or 0), 365))
        descuento_fijo_pct = max(0.0, min(float(body.get('descuento_fijo_pct') or 0.0), 100.0))
    except (ValueError, TypeError):
        raise ComercialError("Los valores numéricos de crédito o descuento no son válidos")

    cust_id = str(uuid.uuid4())

    with database.get_db() as conn:
        # Verificar duplicado en la misma empresa
        dup = conn.execute(
            "SELECT id, nombre, estado FROM sales_customers WHERE company_id = ? AND identificacion = ?",
            (cid, identificacion)
        ).fetchone()
        if dup:
            if dup['estado'] == 'activo':
                raise ComercialError(f"Ya existe un cliente activo con la identificación {identificacion}: '{dup['nombre']}'")
            else:
                # Si estaba inactivo, reactivarlo y actualizarlo
                conn.execute("""
                    UPDATE sales_customers SET
                        tipo_identificacion = ?, nombre = ?, nombre_comercial = ?, email = ?,
                        telefono = ?, provincia = ?, canton = ?, distrito = ?, barrio = ?,
                        otras_senas = ?, direccion = ?, actividad_economica = ?, condicion_venta = ?,
                        limite_credito = ?, plazo_credito = ?, descuento_fijo_pct = ?, estado = 'activo',
                        updated_at = datetime('now','localtime')
                    WHERE id = ? AND company_id = ?
                """, (tipo_id, nombre, nombre_comercial, email, telefono, provincia, canton, distrito,
                      barrio, otras_senas, direccion, actividad_economica, cond_venta, limite_credito,
                      plazo_credito, descuento_fijo_pct, dup['id'], cid))
                database.insert_audit_log(conn, 'ventas_customer_reactivate', actor, 'customer', dup['id'],
                                          {'identificacion': identificacion, 'nombre': nombre})
                return get_customer(cid, dup['id'])

        conn.execute("""
            INSERT INTO sales_customers (
                id, company_id, tipo_identificacion, identificacion, nombre, nombre_comercial,
                email, telefono, provincia, canton, distrito, barrio, otras_senas, direccion,
                actividad_economica, condicion_venta, limite_credito, plazo_credito,
                descuento_fijo_pct, estado
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'activo')
        """, (cust_id, cid, tipo_id, identificacion, nombre, nombre_comercial, email, telefono,
              provincia, canton, distrito, barrio, otras_senas, direccion, actividad_economica,
              cond_venta, limite_credito, plazo_credito, descuento_fijo_pct))

        database.insert_audit_log(conn, 'ventas_customer_create', actor, 'customer', cust_id,
                                  {'identificacion': identificacion, 'nombre': nombre, 'tipo': tipo_id})

    return get_customer(cid, cust_id)


def update_customer(company_id: str, customer_id: str, body: dict, actor: str):
    cid = str(company_id or '').strip()
    cust_id = str(customer_id or '').strip()

    with database.get_db() as conn:
        curr = conn.execute("SELECT * FROM sales_customers WHERE id = ? AND company_id = ?",
                            (cust_id, cid)).fetchone()
        if not curr:
            raise ComercialError("Cliente no encontrado")

        tipo_id = str(body.get('tipo_identificacion') or curr['tipo_identificacion']).strip()
        identificacion = clean_identificacion(tipo_id, body.get('identificacion') or curr['identificacion'])

        # Verificar unicidad si cambió la identificación
        if identificacion != curr['identificacion']:
            dup = conn.execute(
                "SELECT id FROM sales_customers WHERE company_id = ? AND identificacion = ? AND id != ? AND estado = 'activo'",
                (cid, identificacion, cust_id)
            ).fetchone()
            if dup:
                raise ComercialError(f"La identificación {identificacion} ya pertenece a otro cliente")

        nombre = str(body.get('nombre') or curr['nombre']).strip()
        if not nombre:
            raise ComercialError("El nombre es requerido")
        if len(nombre) > 160:
            raise ComercialError("El nombre excede los 160 caracteres")

        nombre_comercial = str(body.get('nombre_comercial', curr['nombre_comercial'] or '')).strip()[:160]

        email = str(body.get('email', curr['email'] or '')).strip()
        if email and (len(email) > 120 or not EMAIL_RE.match(email)):
            raise ComercialError("El correo electrónico no es válido")

        telefono = str(body.get('telefono', curr['telefono'] or '')).strip()[:30]
        provincia = str(body.get('provincia', curr['provincia'] or '1')).strip()[:2]
        canton = str(body.get('canton', curr['canton'] or '01')).strip()[:2]
        distrito = str(body.get('distrito', curr['distrito'] or '01')).strip()[:2]
        barrio = str(body.get('barrio', curr['barrio'] or '')).strip()[:80]
        otras_senas = str(body.get('otras_senas', body.get('direccion', curr['otras_senas'] or ''))).strip()[:250]
        actividad_economica = str(body.get('actividad_economica', curr['actividad_economica'] or '')).strip()[:20]

        cond_venta = str(body.get('condicion_venta') or curr['condicion_venta']).strip()
        if cond_venta not in CONDICIONES_VENTA:
            raise ComercialError(f"Condición de venta '{cond_venta}' inválida")

        limite_credito = max(0.0, float(body.get('limite_credito', curr['limite_credito'] or 0.0)))
        plazo_credito = max(0, min(int(body.get('plazo_credito', curr['plazo_credito'] or 0)), 365))
        descuento_fijo_pct = max(0.0, min(float(body.get('descuento_fijo_pct', curr['descuento_fijo_pct'] or 0.0)), 100.0))
        estado = str(body.get('estado') or curr['estado']).strip()
        if estado not in ('activo', 'inactivo'):
            raise ComercialError("Estado debe ser 'activo' o 'inactivo'")

        conn.execute("""
            UPDATE sales_customers SET
                tipo_identificacion = ?, identificacion = ?, nombre = ?, nombre_comercial = ?,
                email = ?, telefono = ?, provincia = ?, canton = ?, distrito = ?, barrio = ?,
                otras_senas = ?, direccion = ?, actividad_economica = ?, condicion_venta = ?,
                limite_credito = ?, plazo_credito = ?, descuento_fijo_pct = ?, estado = ?,
                updated_at = datetime('now','localtime')
            WHERE id = ? AND company_id = ?
        """, (tipo_id, identificacion, nombre, nombre_comercial, email, telefono, provincia, canton,
              distrito, barrio, otras_senas, otras_senas, actividad_economica, cond_venta,
              limite_credito, plazo_credito, descuento_fijo_pct, estado, cust_id, cid))

        database.insert_audit_log(conn, 'ventas_customer_update', actor, 'customer', cust_id,
                                  {'identificacion': identificacion, 'nombre': nombre, 'estado': estado})

    return get_customer(cid, cust_id)


def delete_customer(company_id: str, customer_id: str, actor: str):
    cid = str(company_id or '').strip()
    cust_id = str(customer_id or '').strip()

    with database.get_db() as conn:
        curr = conn.execute("SELECT * FROM sales_customers WHERE id = ? AND company_id = ?",
                            (cust_id, cid)).fetchone()
        if not curr:
            raise ComercialError("Cliente no encontrado")

        # Verificar si tiene comprobantes o CxC
        docs = conn.execute("SELECT COUNT(*) FROM electronic_documents WHERE company_id = ? AND customer_id = ?",
                            (cid, cust_id)).fetchone()[0]
        cxc = conn.execute("SELECT COUNT(*) FROM accounts_receivable WHERE company_id = ? AND customer_id = ?",
                           (cid, cust_id)).fetchone()[0]

        if docs > 0 or cxc > 0:
            # Soft delete para proteger trazabilidad fiscal y contable
            conn.execute("UPDATE sales_customers SET estado = 'inactivo', updated_at = datetime('now','localtime') WHERE id = ? AND company_id = ?",
                         (cust_id, cid))
            database.insert_audit_log(conn, 'ventas_customer_deactivate', actor, 'customer', cust_id,
                                      {'motivo': 'desactivado_con_historial', 'nombre': curr['nombre']})
            return {'deleted': True, 'mode': 'soft', 'message': 'Cliente desactivado (conserva historial fiscal)'}
        else:
            # Hard delete si no tiene ningún comprobante
            conn.execute("DELETE FROM sales_customers WHERE id = ? AND company_id = ?", (cust_id, cid))
            database.insert_audit_log(conn, 'ventas_customer_delete', actor, 'customer', cust_id,
                                      {'nombre': curr['nombre'], 'identificacion': curr['identificacion']})
            return {'deleted': True, 'mode': 'hard', 'message': 'Cliente eliminado definitivamente'}


# ── Categorías de Productos ───────────────────────────────────────────

def list_categories(company_id: str, activo_only: bool = False):
    cid = str(company_id or '').strip()
    where = "WHERE c.company_id = ?"
    params = [cid]
    if activo_only:
        where += " AND c.activo = 1"

    with database.get_db() as conn:
        query = f"""
            SELECT c.*,
                   COUNT(p.id) AS productos_count
            FROM product_categories c
            LEFT JOIN products p ON p.company_id = c.company_id AND p.category_id = c.id AND p.estado = 'activo'
            {where}
            GROUP BY c.id
            ORDER BY c.nombre COLLATE NOCASE ASC
        """
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]


def create_category(company_id: str, body: dict, actor: str):
    cid = str(company_id or '').strip()
    nombre = str(body.get('nombre') or '').strip()
    if not nombre:
        raise ComercialError("El nombre de la categoría es requerido")
    if len(nombre) > 80:
        raise ComercialError("El nombre de categoría no puede exceder 80 caracteres")

    descripcion = str(body.get('descripcion') or '').strip()[:250]
    cat_id = str(uuid.uuid4())

    with database.get_db() as conn:
        dup = conn.execute("SELECT id FROM product_categories WHERE company_id = ? AND LOWER(nombre) = LOWER(?)",
                           (cid, nombre)).fetchone()
        if dup:
            raise ComercialError(f"Ya existe una categoría llamada '{nombre}'")

        conn.execute("""
            INSERT INTO product_categories (id, company_id, nombre, descripcion, activo)
            VALUES (?, ?, ?, ?, 1)
        """, (cat_id, cid, nombre, descripcion))

        database.insert_audit_log(conn, 'ventas_category_create', actor, 'category', cat_id, {'nombre': nombre})
        return {'id': cat_id, 'company_id': cid, 'nombre': nombre, 'descripcion': descripcion, 'activo': 1}


def update_category(company_id: str, category_id: str, body: dict, actor: str):
    cid = str(company_id or '').strip()
    cat_id = str(category_id or '').strip()

    with database.get_db() as conn:
        curr = conn.execute("SELECT * FROM product_categories WHERE id = ? AND company_id = ?",
                            (cat_id, cid)).fetchone()
        if not curr:
            raise ComercialError("Categoría no encontrada")

        nombre = str(body.get('nombre') or curr['nombre']).strip()
        if not nombre:
            raise ComercialError("El nombre es requerido")
        if len(nombre) > 80:
            raise ComercialError("El nombre no puede exceder 80 caracteres")

        dup = conn.execute(
            "SELECT id FROM product_categories WHERE company_id = ? AND LOWER(nombre) = LOWER(?) AND id != ?",
            (cid, nombre, cat_id)
        ).fetchone()
        if dup:
            raise ComercialError(f"Ya existe otra categoría llamada '{nombre}'")

        descripcion = str(body.get('descripcion', curr['descripcion'] or '')).strip()[:250]
        activo = 1 if body.get('activo', curr['activo']) in (1, '1', True) else 0

        conn.execute("""
            UPDATE product_categories SET nombre = ?, descripcion = ?, activo = ?
            WHERE id = ? AND company_id = ?
        """, (nombre, descripcion, activo, cat_id, cid))

        # Actualizar denormalizado en products
        conn.execute("""
            UPDATE products SET categoria_nombre = ?
            WHERE category_id = ? AND company_id = ?
        """, (nombre, cat_id, cid))

        database.insert_audit_log(conn, 'ventas_category_update', actor, 'category', cat_id,
                                  {'nombre': nombre, 'activo': activo})
        return {'id': cat_id, 'company_id': cid, 'nombre': nombre, 'descripcion': descripcion, 'activo': activo}


def delete_category(company_id: str, category_id: str, actor: str):
    cid = str(company_id or '').strip()
    cat_id = str(category_id or '').strip()

    with database.get_db() as conn:
        curr = conn.execute("SELECT * FROM product_categories WHERE id = ? AND company_id = ?",
                            (cat_id, cid)).fetchone()
        if not curr:
            raise ComercialError("Categoría no encontrada")

        # Verificar si hay productos asignados
        p_count = conn.execute("SELECT COUNT(*) FROM products WHERE category_id = ? AND company_id = ?",
                               (cat_id, cid)).fetchone()[0]
        if p_count > 0:
            raise ComercialError(f"No se puede eliminar la categoría '{curr['nombre']}': tiene {p_count} producto(s) asignado(s)")

        conn.execute("DELETE FROM product_categories WHERE id = ? AND company_id = ?", (cat_id, cid))
        database.insert_audit_log(conn, 'ventas_category_delete', actor, 'category', cat_id, {'nombre': curr['nombre']})
        return {'deleted': True}


# ── Catálogo de Productos y Servicios ─────────────────────────────────

def list_products(company_id: str, q: str = None, category_id: str = None, tipo: str = None,
                  stock_bajo: bool = False, estado: str = 'activo', limit: int = 100, offset: int = 0):
    cid = str(company_id or '').strip()
    limit = max(1, min(int(limit or 100), 500))
    offset = max(0, int(offset or 0))

    where = ["company_id = ?"]
    params = [cid]

    if estado and estado != 'todos':
        where.append("estado = ?")
        params.append(estado)

    if category_id and category_id.strip():
        where.append("category_id = ?")
        params.append(category_id.strip())

    if tipo and tipo.strip() in ('mercancia', 'servicio'):
        where.append("tipo = ?")
        params.append(tipo.strip())

    if stock_bajo:
        where.append("controla_inventario = 1 AND existencia <= stock_minimo")

    if q and q.strip():
        term = f"%{q.strip()}%"
        where.append("(codigo_interno LIKE ? OR codigo_cabys LIKE ? OR codigo_barras LIKE ? OR nombre LIKE ? OR descripcion LIKE ?)")
        params.extend([term, term, term, term, term])

    where_sql = " AND ".join(where)

    with database.get_db() as conn:
        total = conn.execute(f"SELECT COUNT(*) FROM products WHERE {where_sql}", params).fetchone()[0]

        query = f"""
            SELECT *
            FROM products
            WHERE {where_sql}
            ORDER BY nombre COLLATE NOCASE ASC
            LIMIT ? OFFSET ?
        """
        rows = conn.execute(query, params + [limit, offset]).fetchall()

        items = []
        for r in rows:
            d = dict(r)
            pv = float(d.get('precio_venta') or 0.0)
            iva_pct = float(d.get('tarifa_iva_porcentaje') or 0.0)
            d['precio_con_iva'] = round(pv * (1 + (iva_pct / 100.0)), 2)

            stock = float(d.get('existencia') or 0.0)
            stock_min = float(d.get('stock_minimo') or 0.0)
            ctrl = bool(d.get('controla_inventario'))
            d['alerta_stock_bajo'] = ctrl and (stock <= stock_min)
            d['sin_stock'] = ctrl and (stock <= 0)
            items.append(d)

        return {'items': items, 'total': total, 'limit': limit, 'offset': offset}


def get_product(company_id: str, product_id: str):
    cid = str(company_id or '').strip()
    pid = str(product_id or '').strip()

    with database.get_db() as conn:
        r = conn.execute("SELECT * FROM products WHERE id = ? AND company_id = ?", (pid, cid)).fetchone()
        if not r:
            raise ComercialError("Producto o servicio no encontrado")

        d = dict(r)
        pv = float(d.get('precio_venta') or 0.0)
        iva_pct = float(d.get('tarifa_iva_porcentaje') or 0.0)
        d['precio_con_iva'] = round(pv * (1 + (iva_pct / 100.0)), 2)

        stock = float(d.get('existencia') or 0.0)
        stock_min = float(d.get('stock_minimo') or 0.0)
        ctrl = bool(d.get('controla_inventario'))
        d['alerta_stock_bajo'] = ctrl and (stock <= stock_min)
        d['sin_stock'] = ctrl and (stock <= 0)

        # Historial de ventas de este producto
        movs = conn.execute("""
            SELECT l.document_id, l.cantidad, l.precio_unitario, l.total_linea, ed.fecha_emision, ed.consecutivo, ed.tipo_documento
            FROM electronic_document_lines l
            JOIN electronic_documents ed ON ed.id = l.document_id AND ed.company_id = l.company_id
            WHERE l.company_id = ? AND l.product_id = ?
            ORDER BY ed.fecha_emision DESC
            LIMIT 10
        """, (cid, pid)).fetchall()
        d['ultimas_ventas'] = [dict(m) for m in movs]

        return d


def create_product(company_id: str, body: dict, actor: str):
    cid = str(company_id or '').strip()
    if not cid:
        raise ComercialError("company_id es requerido")

    cod_int = str(body.get('codigo_interno') or '').strip()
    if not cod_int:
        raise ComercialError("El código interno / SKU es requerido")
    if len(cod_int) > 40:
        raise ComercialError("El código interno no puede exceder 40 caracteres")

    cabys = clean_cabys(body.get('codigo_cabys'))

    nombre = str(body.get('nombre') or '').strip()
    if not nombre:
        raise ComercialError("El nombre del producto o servicio es requerido")
    if len(nombre) > 160:
        raise ComercialError("El nombre no puede exceder 160 caracteres")

    descripcion = str(body.get('descripcion') or '').strip()[:300]
    tipo = str(body.get('tipo') or 'mercancia').strip().lower()
    if tipo not in ('mercancia', 'servicio'):
        raise ComercialError("El tipo debe ser 'mercancia' o 'servicio'")

    unidad = str(body.get('unidad_medida') or ('Sp' if tipo == 'servicio' else 'Unid')).strip()
    moneda = str(body.get('moneda') or 'CRC').strip().upper()
    if moneda not in ('CRC', 'USD'):
        raise ComercialError("Moneda debe ser CRC o USD")

    # Tarifas de IVA
    iva_cod = str(body.get('tarifa_iva_codigo') or '08').strip()
    with database.get_db() as conn:
        tr = conn.execute("SELECT porcentaje FROM tax_rates WHERE codigo = ? AND activo = 1", (iva_cod,)).fetchone()
        if not tr:
            raise ComercialError(f"Código de tarifa de IVA '{iva_cod}' no válido en Costa Rica")
        iva_pct = float(tr['porcentaje'])

        # Precios y costos
        try:
            precio_compra = max(0.0, float(body.get('precio_compra') or 0.0))
            precio_venta = max(0.0, float(body.get('precio_venta') or 0.0))
            precio_mayorista = max(0.0, float(body.get('precio_mayorista') or 0.0))
            precio_especial = max(0.0, float(body.get('precio_especial') or 0.0))
            desc_max = max(0.0, min(float(body.get('descuento_maximo') or 0.0), 100.0))
        except (ValueError, TypeError):
            raise ComercialError("Los precios y costos deben ser valores numéricos válidos")

        # Margen comercial
        if precio_compra > 0:
            margen = round(((precio_venta - precio_compra) / precio_compra) * 100.0, 2)
        else:
            margen = 0.0

        # Inventario
        if tipo == 'servicio':
            ctrl_inv = 0
            existencia = 0.0
            stock_min = 0.0
            stock_max = 0.0
        else:
            ctrl_inv = 1 if body.get('controla_inventario', 1) in (1, '1', True) else 0
            try:
                existencia = max(0.0, float(body.get('existencia') or 0.0)) if ctrl_inv else 0.0
                stock_min = max(0.0, float(body.get('stock_minimo') or 0.0)) if ctrl_inv else 0.0
                stock_max = max(0.0, float(body.get('stock_maximo') or 0.0)) if ctrl_inv else 0.0
            except (ValueError, TypeError):
                raise ComercialError("Los valores de stock deben ser numéricos")

        # Categoría
        cat_id = str(body.get('category_id') or '').strip()
        cat_nom = 'General'
        if cat_id:
            c_row = conn.execute("SELECT nombre FROM product_categories WHERE id = ? AND company_id = ?",
                                 (cat_id, cid)).fetchone()
            if c_row:
                cat_nom = c_row['nombre']
            else:
                cat_id = ''

        # Unicidad de SKU por empresa
        dup = conn.execute("SELECT id FROM products WHERE company_id = ? AND codigo_interno = ?",
                           (cid, cod_int)).fetchone()
        if dup:
            raise ComercialError(f"El código interno '{cod_int}' ya está registrado en esta empresa")

        pid = str(uuid.uuid4())
        cod_barras = str(body.get('codigo_barras') or '').strip()[:40]

        conn.execute("""
            INSERT INTO products (
                id, company_id, codigo_interno, codigo_barras, codigo_cabys, nombre, descripcion,
                tipo, category_id, categoria_nombre, unidad_medida, moneda, precio_compra,
                precio_venta, precio_mayorista, precio_especial, descuento_maximo, margen_comercial,
                tarifa_iva_codigo, tarifa_iva_porcentaje, existencia, stock_minimo, stock_maximo,
                controla_inventario, estado
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'activo')
        """, (pid, cid, cod_int, cod_barras, cabys, nombre, descripcion, tipo, cat_id, cat_nom,
              unidad, moneda, precio_compra, precio_venta, precio_mayorista, precio_especial,
              desc_max, margen, iva_cod, iva_pct, existencia, stock_min, stock_max, ctrl_inv))

        database.insert_audit_log(conn, 'ventas_product_create', actor, 'product', pid,
                                  {'codigo_interno': cod_int, 'cabys': cabys, 'nombre': nombre,
                                   'precio_venta': precio_venta, 'iva_cod': iva_cod})

    return get_product(cid, pid)


def update_product(company_id: str, product_id: str, body: dict, actor: str):
    cid = str(company_id or '').strip()
    pid = str(product_id or '').strip()

    with database.get_db() as conn:
        curr = conn.execute("SELECT * FROM products WHERE id = ? AND company_id = ?", (pid, cid)).fetchone()
        if not curr:
            raise ComercialError("Producto o servicio no encontrado")

        cod_int = str(body.get('codigo_interno') or curr['codigo_interno']).strip()
        if not cod_int:
            raise ComercialError("El código interno es requerido")

        # Comprobar unicidad si cambió
        if cod_int != curr['codigo_interno']:
            dup = conn.execute("SELECT id FROM products WHERE company_id = ? AND codigo_interno = ? AND id != ?",
                               (cid, cod_int, pid)).fetchone()
            if dup:
                raise ComercialError(f"El código interno '{cod_int}' ya pertenece a otro producto")

        cabys = clean_cabys(body.get('codigo_cabys') or curr['codigo_cabys'])

        nombre = str(body.get('nombre') or curr['nombre']).strip()
        if not nombre:
            raise ComercialError("El nombre es requerido")

        descripcion = str(body.get('descripcion', curr['descripcion'] or '')).strip()[:300]
        tipo = str(body.get('tipo') or curr['tipo'] or 'mercancia').strip().lower()
        unidad = str(body.get('unidad_medida') or curr['unidad_medida']).strip()
        moneda = str(body.get('moneda') or curr['moneda'] or 'CRC').strip().upper()

        iva_cod = str(body.get('tarifa_iva_codigo') or curr['tarifa_iva_codigo']).strip()
        tr = conn.execute("SELECT porcentaje FROM tax_rates WHERE codigo = ? AND activo = 1", (iva_cod,)).fetchone()
        if not tr:
            raise ComercialError(f"Código de tarifa de IVA '{iva_cod}' inválido")
        iva_pct = float(tr['porcentaje'])

        precio_compra = max(0.0, float(body.get('precio_compra', curr['precio_compra'] or 0.0)))
        precio_venta = max(0.0, float(body.get('precio_venta', curr['precio_venta'] or 0.0)))
        precio_mayorista = max(0.0, float(body.get('precio_mayorista', curr['precio_mayorista'] or 0.0)))
        precio_especial = max(0.0, float(body.get('precio_especial', curr['precio_especial'] or 0.0)))
        desc_max = max(0.0, min(float(body.get('descuento_maximo', curr['descuento_maximo'] or 0.0)), 100.0))

        if precio_compra > 0:
            margen = round(((precio_venta - precio_compra) / precio_compra) * 100.0, 2)
        else:
            margen = 0.0

        if tipo == 'servicio':
            ctrl_inv = 0
            existencia = 0.0
            stock_min = 0.0
            stock_max = 0.0
        else:
            ctrl_inv = 1 if body.get('controla_inventario', curr['controla_inventario']) in (1, '1', True) else 0
            existencia = max(0.0, float(body.get('existencia', curr['existencia'] or 0.0))) if ctrl_inv else 0.0
            stock_min = max(0.0, float(body.get('stock_minimo', curr['stock_minimo'] or 0.0))) if ctrl_inv else 0.0
            stock_max = max(0.0, float(body.get('stock_maximo', curr['stock_maximo'] or 0.0))) if ctrl_inv else 0.0

        cat_id = str(body.get('category_id', curr['category_id'] or '')).strip()
        cat_nom = curr['categoria_nombre'] or 'General'
        if cat_id:
            c_row = conn.execute("SELECT nombre FROM product_categories WHERE id = ? AND company_id = ?",
                                 (cat_id, cid)).fetchone()
            if c_row:
                cat_nom = c_row['nombre']
            else:
                cat_id = ''
                cat_nom = 'General'

        cod_barras = str(body.get('codigo_barras', curr['codigo_barras'] or '')).strip()[:40]
        estado = str(body.get('estado') or curr['estado']).strip()
        if estado not in ('activo', 'inactivo'):
            raise ComercialError("Estado debe ser 'activo' o 'inactivo'")

        conn.execute("""
            UPDATE products SET
                codigo_interno = ?, codigo_barras = ?, codigo_cabys = ?, nombre = ?, descripcion = ?,
                tipo = ?, category_id = ?, categoria_nombre = ?, unidad_medida = ?, moneda = ?,
                precio_compra = ?, precio_venta = ?, precio_mayorista = ?, precio_especial = ?,
                descuento_maximo = ?, margen_comercial = ?, tarifa_iva_codigo = ?, tarifa_iva_porcentaje = ?,
                existencia = ?, stock_minimo = ?, stock_maximo = ?, controla_inventario = ?, estado = ?,
                updated_at = datetime('now','localtime')
            WHERE id = ? AND company_id = ?
        """, (cod_int, cod_barras, cabys, nombre, descripcion, tipo, cat_id, cat_nom, unidad, moneda,
              precio_compra, precio_venta, precio_mayorista, precio_especial, desc_max, margen,
              iva_cod, iva_pct, existencia, stock_min, stock_max, ctrl_inv, estado, pid, cid))

        database.insert_audit_log(conn, 'ventas_product_update', actor, 'product', pid,
                                  {'codigo_interno': cod_int, 'nombre': nombre, 'estado': estado,
                                   'precio_venta': precio_venta})

    return get_product(cid, pid)


def delete_product(company_id: str, product_id: str, actor: str):
    cid = str(company_id or '').strip()
    pid = str(product_id or '').strip()

    with database.get_db() as conn:
        curr = conn.execute("SELECT * FROM products WHERE id = ? AND company_id = ?", (pid, cid)).fetchone()
        if not curr:
            raise ComercialError("Producto no encontrado")

        # Verificar si ha sido utilizado en comprobantes
        lines = conn.execute("SELECT COUNT(*) FROM electronic_document_lines WHERE company_id = ? AND product_id = ?",
                             (cid, pid)).fetchone()[0]

        if lines > 0:
            # Soft delete para proteger historial contable y fiscal
            conn.execute("UPDATE products SET estado = 'inactivo', updated_at = datetime('now','localtime') WHERE id = ? AND company_id = ?",
                         (pid, cid))
            database.insert_audit_log(conn, 'ventas_product_deactivate', actor, 'product', pid,
                                      {'motivo': 'desactivado_con_historial_ventas', 'nombre': curr['nombre']})
            return {'deleted': True, 'mode': 'soft', 'message': 'Producto desactivado (conserva historial de ventas)'}
        else:
            # Hard delete
            conn.execute("DELETE FROM products WHERE id = ? AND company_id = ?", (pid, cid))
            database.insert_audit_log(conn, 'ventas_product_delete', actor, 'product', pid,
                                      {'nombre': curr['nombre'], 'codigo_interno': curr['codigo_interno']})
            return {'deleted': True, 'mode': 'hard', 'message': 'Producto eliminado definitivamente'}


def adjust_stock(company_id: str, product_id: str, body: dict, actor: str):
    cid = str(company_id or '').strip()
    pid = str(product_id or '').strip()

    try:
        nuevo_stock = max(0.0, float(body.get('nuevo_stock', 0.0)))
    except (ValueError, TypeError):
        raise ComercialError("El nuevo stock debe ser un número válido")

    motivo = str(body.get('motivo') or 'Ajuste manual de inventario').strip()[:200]

    with database.get_db() as conn:
        curr = conn.execute("SELECT * FROM products WHERE id = ? AND company_id = ?", (pid, cid)).fetchone()
        if not curr:
            raise ComercialError("Producto no encontrado")
        if not curr['controla_inventario']:
            raise ComercialError("Este producto o servicio no controla inventario")

        stock_anterior = float(curr['existencia'] or 0.0)
        diferencia = round(nuevo_stock - stock_anterior, 4)

        conn.execute("""
            UPDATE products SET existencia = ?, updated_at = datetime('now','localtime')
            WHERE id = ? AND company_id = ?
        """, (nuevo_stock, pid, cid))

        database.insert_audit_log(conn, 'ventas_stock_adjust', actor, 'product', pid,
                                  {'stock_anterior': stock_anterior, 'nuevo_stock': nuevo_stock,
                                   'diferencia': diferencia, 'motivo': motivo})

    return get_product(cid, pid)


# ── Catálogos y Parámetros Auxiliares ───────────────────────────────────

def get_tax_rates():
    with database.get_db() as conn:
        rows = conn.execute("SELECT codigo, descripcion, porcentaje FROM tax_rates WHERE activo = 1 ORDER BY porcentaje ASC").fetchall()
        return [dict(r) for r in rows]


def get_unidades_medida():
    return list(UNIDADES_MEDIDA)
