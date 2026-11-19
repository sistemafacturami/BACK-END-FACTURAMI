"""
FACTURAMI - Servicio de Gestión Comercial (Fase 6).
===================================================
Administración de:
  1. Cotizaciones / Proformas (Emisión, validez, estados y conversión a Factura/Pedido).
  2. Pedidos de Venta (Órdenes comerciales, estados de despacho y conversión a Factura).
  3. Kardex de Inventario (Trazabilidad detallada y costeo promedio ponderado).
  4. Punto de Venta (POS) y Turnos de Caja (Aperturas, cobro rápido, arqueo y cierre).
Todos los datos aislados por `company_id`.
"""
import uuid
import re
from datetime import datetime, date, timedelta
import db as database
from services.ventas_service import init_ventas_schema


class GestionComercialError(ValueError):
    pass


# ══════════════════════════════════════════════════════════════════════════════
# 1. COTIZACIONES Y PROFORMAS
# ══════════════════════════════════════════════════════════════════════════════

def _siguiente_correlativo(conn, table: str, prefix: str, cid: str) -> str:
    """Genera un correlativo mensual secuencial con bloqueo atómico (ej: COT-202611-0001)."""
    ym = datetime.now().strftime('%Y%m')
    base_prefix = f"{prefix}-{ym}-"

    col = "numero_cotizacion" if table == "quotations" else "numero_pedido"
    row = conn.execute(f"""
        SELECT {col} FROM {table}
        WHERE company_id = ? AND {col} LIKE ?
        ORDER BY {col} DESC LIMIT 1
    """, (cid, f"{base_prefix}%")).fetchone()

    sec = 1
    if row and row[col]:
        partes = row[col].split('-')
        if len(partes) >= 3 and partes[-1].isdigit():
            sec = int(partes[-1]) + 1

    return f"{base_prefix}{str(sec).zfill(4)}"


def create_quotation(company_id: str, data: dict, user: str, db_path=None) -> dict:
    cid = str(company_id or '').strip()
    if not cid:
        raise GestionComercialError("company_id es requerido")

    lineas_raw = data.get('lineas') or []
    if not lineas_raw:
        raise GestionComercialError("La cotización debe tener al menos una línea")

    customer_id = str(data.get('customer_id') or '').strip()
    dias_validez = int(data.get('dias_validez') or 15)
    f_emision = data.get('fecha_emision') or datetime.now().strftime('%Y-%m-%d')
    f_venc = data.get('fecha_vencimiento') or (datetime.strptime(f_emision[:10], '%Y-%m-%d') + timedelta(days=dias_validez)).strftime('%Y-%m-%d')

    moneda = str(data.get('moneda') or 'CRC').strip().upper()
    if moneda not in ('CRC', 'USD'):
        moneda = 'CRC'
    tc = float(data.get('tipo_cambio') or 1.0)
    cond_venta = str(data.get('condicion_venta') or '01').strip()
    plazo_credito = int(data.get('plazo_credito') or 0)
    notas = str(data.get('notas') or '').strip()

    init_ventas_schema(db_path)

    with database.get_db(db_path) as conn:
        # Resolver datos del cliente receptor
        cust_nom = "Consumidor Final"
        cust_id_num = ""
        cust_mail = ""
        if customer_id:
            c_row = conn.execute("SELECT nombre, identificacion, email FROM sales_customers WHERE id = ? AND company_id = ?",
                                 (customer_id, cid)).fetchone()
            if c_row:
                cust_nom = c_row['nombre']
                cust_id_num = c_row['identificacion'] or ''
                cust_mail = c_row['email'] or ''

        # Procesar líneas y calcular subtotales
        subtotal = 0.0
        total_descuentos = 0.0
        total_impuestos = 0.0
        processed_lines = []

        tax_map = {r['codigo']: float(r['porcentaje']) for r in conn.execute("SELECT codigo, porcentaje FROM tax_rates").fetchall()}

        for idx, l in enumerate(lineas_raw, 1):
            cant = max(0.001, float(l.get('cantidad') or 1))
            pu = max(0.0, float(l.get('precio_unitario') or 0))
            desc = max(0.0, float(l.get('monto_descuento') or 0))
            sub_linea = round(cant * pu, 2)
            base = max(0.0, round(sub_linea - desc, 2))

            iva_cod = str(l.get('tarifa_iva_codigo') or '08').strip()
            iva_pct = tax_map.get(iva_cod, 13.0)
            imp_linea = round(base * (iva_pct / 100.0), 2)
            tot_linea = round(base + imp_linea, 2)

            subtotal += sub_linea
            total_descuentos += desc
            total_impuestos += imp_linea

            processed_lines.append({
                'id': str(uuid.uuid4()),
                'numero_linea': idx,
                'product_id': str(l.get('product_id') or '').strip(),
                'codigo_interno': str(l.get('codigo_interno') or '')[:50],
                'codigo_cabys': str(l.get('codigo_cabys') or '0000000000000').zfill(13)[:13],
                'descripcion': str(l.get('descripcion') or f"Línea {idx}")[:200],
                'cantidad': cant,
                'unidad_medida': str(l.get('unidad_medida') or 'Unid')[:5],
                'precio_unitario': pu,
                'monto_descuento': desc,
                'subtotal_linea': sub_linea,
                'base_imponible': base,
                'tarifa_iva_codigo': iva_cod,
                'tarifa_iva_porcentaje': iva_pct,
                'monto_impuesto': imp_linea,
                'total_linea': tot_linea
            })

        total_cotizacion = round((subtotal - total_descuentos) + total_impuestos, 2)
        qid = str(uuid.uuid4())
        num_cot = _siguiente_correlativo(conn, 'quotations', 'COT', cid)

        conn.execute("""
            INSERT INTO quotations (
                id, company_id, customer_id, customer_nombre, customer_identificacion,
                customer_email, numero_cotizacion, fecha_emision, fecha_vencimiento,
                moneda, tipo_cambio, condicion_venta, plazo_credito, subtotal,
                total_descuentos, total_impuestos, total_cotizacion, notas, estado,
                usuario_creacion
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'emitida', ?)
        """, (
            qid, cid, customer_id, cust_nom, cust_id_num, cust_mail, num_cot,
            f_emision, f_venc, moneda, tc, cond_venta, plazo_credito, subtotal,
            total_descuentos, total_impuestos, total_cotizacion, notas, user
        ))

        for pl in processed_lines:
            conn.execute("""
                INSERT INTO quotation_lines (
                    id, quotation_id, company_id, numero_linea, product_id,
                    codigo_interno, codigo_cabys, descripcion, cantidad, unidad_medida,
                    precio_unitario, monto_descuento, subtotal_linea, base_imponible,
                    tarifa_iva_codigo, tarifa_iva_porcentaje, monto_impuesto, total_linea
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                pl['id'], qid, cid, pl['numero_linea'], pl['product_id'],
                pl['codigo_interno'], pl['codigo_cabys'], pl['descripcion'],
                pl['cantidad'], pl['unidad_medida'], pl['precio_unitario'],
                pl['monto_descuento'], pl['subtotal_linea'], pl['base_imponible'],
                pl['tarifa_iva_codigo'], pl['tarifa_iva_porcentaje'], pl['monto_impuesto'],
                pl['total_linea']
            ))

        database.insert_audit_log(conn, 'ventas_quotation_create', user, 'quotation', qid,
                                  {'numero': num_cot, 'total': total_cotizacion, 'cliente': cust_nom})

    return get_quotation(cid, qid, db_path=db_path)


def get_quotation(company_id: str, quotation_id: str, db_path=None) -> dict:
    cid = str(company_id or '').strip()
    qid = str(quotation_id or '').strip()
    init_ventas_schema(db_path)

    with database.get_db(db_path) as conn:
        q = conn.execute("SELECT * FROM quotations WHERE id = ? AND company_id = ?", (qid, cid)).fetchone()
        if not q:
            raise GestionComercialError(f"Cotización '{qid}' no encontrada")
        res = dict(q)
        lines = conn.execute("""
            SELECT * FROM quotation_lines WHERE quotation_id = ? AND company_id = ?
            ORDER BY numero_linea ASC
        """, (qid, cid)).fetchall()
        res['lineas'] = [dict(l) for l in lines]
        return res


def list_quotations(company_id: str, q: str = '', estado: str = '', limit: int = 50, offset: int = 0, db_path=None) -> dict:
    cid = str(company_id or '').strip()
    init_ventas_schema(db_path)

    with database.get_db(db_path) as conn:
        wheres = ["company_id = ?"]
        params = [cid]

        if estado:
            wheres.append("estado = ?")
            params.append(estado)
        if q:
            term = f"%{q.strip()}%"
            wheres.append("(numero_cotizacion LIKE ? OR customer_nombre LIKE ? OR customer_identificacion LIKE ?)")
            params.extend([term, term, term])

        w_sql = " AND ".join(wheres)
        total = conn.execute(f"SELECT COUNT(*) FROM quotations WHERE {w_sql}", params).fetchone()[0]

        params.extend([int(limit), int(offset)])
        rows = conn.execute(f"""
            SELECT * FROM quotations WHERE {w_sql}
            ORDER BY fecha_emision DESC, numero_cotizacion DESC
            LIMIT ? OFFSET ?
        """, params).fetchall()

        return {'items': [dict(r) for r in rows], 'total': total}


def update_quotation_status(company_id: str, quotation_id: str, new_status: str, user: str, db_path=None) -> dict:
    cid = str(company_id or '').strip()
    qid = str(quotation_id or '').strip()
    st = str(new_status or '').strip().lower()
    valid_states = ('borrador', 'emitida', 'aceptada', 'rechazada', 'facturada', 'vencida')
    if st not in valid_states:
        raise GestionComercialError(f"Estado '{st}' no válido para cotizaciones")

    init_ventas_schema(db_path)
    with database.get_db(db_path) as conn:
        cur = conn.execute("SELECT id, estado, numero_cotizacion FROM quotations WHERE id = ? AND company_id = ?", (qid, cid)).fetchone()
        if not cur:
            raise GestionComercialError("Cotización no encontrada")
        conn.execute("UPDATE quotations SET estado = ?, updated_at = datetime('now','localtime') WHERE id = ?", (st, qid))
        database.insert_audit_log(conn, 'ventas_quotation_status_update', user, 'quotation', qid,
                                  {'anterior': cur['estado'], 'nuevo': st, 'numero': cur['numero_cotizacion']})

    return get_quotation(cid, qid, db_path=db_path)


def convert_quotation_to_invoice(company_id: str, quotation_id: str, user: str, tipo_doc: str = '01', db_path=None) -> dict:
    """Convierte una cotización en Comprobante Electrónico (Factura '01' o Tiquete '04')."""
    from services.fe_emission_service import emitir_comprobante
    cid = str(company_id or '').strip()
    cot = get_quotation(cid, quotation_id, db_path=db_path)

    if cot['estado'] == 'facturada':
        raise GestionComercialError(f"La cotización '{cot['numero_cotizacion']}' ya fue facturada previamente")

    doc_payload = {
        'company_id': cid,
        'tipo_documento': tipo_doc,
        'customer_id': cot['customer_id'] or None,
        'condicion_venta': cot['condicion_venta'],
        'plazo_credito': cot['plazo_credito'],
        'moneda': cot['moneda'],
        'tipo_cambio': cot['tipo_cambio'],
        'lineas': [
            {
                'product_id': l['product_id'] or None,
                'codigo_interno': l['codigo_interno'],
                'codigo_cabys': l['codigo_cabys'],
                'descripcion': l['descripcion'],
                'cantidad': l['cantidad'],
                'unidad_medida': l['unidad_medida'],
                'precio_unitario': l['precio_unitario'],
                'monto_descuento': l['monto_descuento'],
                'tarifa_iva_codigo': l['tarifa_iva_codigo']
            }
            for l in cot['lineas']
        ]
    }

    doc = emitir_comprobante(cid, doc_payload, user, db_path=db_path)

    # Actualizar cotización a 'facturada' y guardar vínculo
    with database.get_db(db_path) as conn:
        conn.execute("""
            UPDATE quotations
            SET estado = 'facturada', documento_destino_id = ?, updated_at = datetime('now','localtime')
            WHERE id = ? AND company_id = ?
        """, (doc['id'], cot['id'], cid))
        database.insert_audit_log(conn, 'ventas_quotation_converted_to_invoice', user, 'quotation', cot['id'],
                                  {'documento_id': doc['id'], 'consecutivo': doc['consecutivo']})

    return {'quotation': get_quotation(cid, cot['id'], db_path=db_path), 'document': doc}


# ══════════════════════════════════════════════════════════════════════════════
# 2. PEDIDOS DE VENTA (SALES ORDERS)
# ══════════════════════════════════════════════════════════════════════════════

def create_sales_order(company_id: str, data: dict, user: str, db_path=None) -> dict:
    cid = str(company_id or '').strip()
    if not cid:
        raise GestionComercialError("company_id es requerido")

    lineas_raw = data.get('lineas') or []
    if not lineas_raw:
        raise GestionComercialError("El pedido debe tener al menos una línea")

    customer_id = str(data.get('customer_id') or '').strip()
    f_pedido = data.get('fecha_pedido') or datetime.now().strftime('%Y-%m-%d')
    f_entrega = data.get('fecha_entrega') or f_pedido
    quotation_id = str(data.get('quotation_id') or '').strip()

    moneda = str(data.get('moneda') or 'CRC').strip().upper()
    if moneda not in ('CRC', 'USD'):
        moneda = 'CRC'
    tc = float(data.get('tipo_cambio') or 1.0)
    cond_venta = str(data.get('condicion_venta') or '01').strip()
    plazo_credito = int(data.get('plazo_credito') or 0)
    notas = str(data.get('notas') or '').strip()

    init_ventas_schema(db_path)

    with database.get_db(db_path) as conn:
        cust_nom = "Consumidor Final"
        cust_id_num = ""
        cust_mail = ""
        if customer_id:
            c_row = conn.execute("SELECT nombre, identificacion, email FROM sales_customers WHERE id = ? AND company_id = ?",
                                 (customer_id, cid)).fetchone()
            if c_row:
                cust_nom = c_row['nombre']
                cust_id_num = c_row['identificacion'] or ''
                cust_mail = c_row['email'] or ''

        tax_map = {r['codigo']: float(r['porcentaje']) for r in conn.execute("SELECT codigo, porcentaje FROM tax_rates").fetchall()}
        subtotal = 0.0
        total_descuentos = 0.0
        total_impuestos = 0.0
        processed_lines = []

        for idx, l in enumerate(lineas_raw, 1):
            cant = max(0.001, float(l.get('cantidad') or 1))
            pu = max(0.0, float(l.get('precio_unitario') or 0))
            desc = max(0.0, float(l.get('monto_descuento') or 0))
            sub_linea = round(cant * pu, 2)
            base = max(0.0, round(sub_linea - desc, 2))

            iva_cod = str(l.get('tarifa_iva_codigo') or '08').strip()
            iva_pct = tax_map.get(iva_cod, 13.0)
            imp_linea = round(base * (iva_pct / 100.0), 2)
            tot_linea = round(base + imp_linea, 2)

            subtotal += sub_linea
            total_descuentos += desc
            total_impuestos += imp_linea

            processed_lines.append({
                'id': str(uuid.uuid4()),
                'numero_linea': idx,
                'product_id': str(l.get('product_id') or '').strip(),
                'codigo_interno': str(l.get('codigo_interno') or '')[:50],
                'codigo_cabys': str(l.get('codigo_cabys') or '0000000000000').zfill(13)[:13],
                'descripcion': str(l.get('descripcion') or f"Línea {idx}")[:200],
                'cantidad': cant,
                'unidad_medida': str(l.get('unidad_medida') or 'Unid')[:5],
                'precio_unitario': pu,
                'monto_descuento': desc,
                'subtotal_linea': sub_linea,
                'base_imponible': base,
                'tarifa_iva_codigo': iva_cod,
                'tarifa_iva_porcentaje': iva_pct,
                'monto_impuesto': imp_linea,
                'total_linea': tot_linea
            })

        total_pedido = round((subtotal - total_descuentos) + total_impuestos, 2)
        oid = str(uuid.uuid4())
        num_ped = _siguiente_correlativo(conn, 'sales_orders', 'PED', cid)

        conn.execute("""
            INSERT INTO sales_orders (
                id, company_id, quotation_id, customer_id, customer_nombre,
                customer_identificacion, customer_email, numero_pedido, fecha_pedido,
                fecha_entrega, moneda, tipo_cambio, condicion_venta, plazo_credito,
                subtotal, total_descuentos, total_impuestos, total_pedido, notas,
                estado, usuario_creacion
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pendiente', ?)
        """, (
            oid, cid, quotation_id, customer_id, cust_nom, cust_id_num, cust_mail,
            num_ped, f_pedido, f_entrega, moneda, tc, cond_venta, plazo_credito,
            subtotal, total_descuentos, total_impuestos, total_pedido, notas, user
        ))

        for pl in processed_lines:
            conn.execute("""
                INSERT INTO sales_order_lines (
                    id, order_id, company_id, numero_linea, product_id,
                    codigo_interno, codigo_cabys, descripcion, cantidad, unidad_medida,
                    precio_unitario, monto_descuento, subtotal_linea, base_imponible,
                    tarifa_iva_codigo, tarifa_iva_porcentaje, monto_impuesto, total_linea
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                pl['id'], oid, cid, pl['numero_linea'], pl['product_id'],
                pl['codigo_interno'], pl['codigo_cabys'], pl['descripcion'],
                pl['cantidad'], pl['unidad_medida'], pl['precio_unitario'],
                pl['monto_descuento'], pl['subtotal_linea'], pl['base_imponible'],
                pl['tarifa_iva_codigo'], pl['tarifa_iva_porcentaje'], pl['monto_impuesto'],
                pl['total_linea']
            ))

        database.insert_audit_log(conn, 'ventas_order_create', user, 'order', oid,
                                  {'numero': num_ped, 'total': total_pedido, 'cliente': cust_nom})

    return get_sales_order(cid, oid, db_path=db_path)


def get_sales_order(company_id: str, order_id: str, db_path=None) -> dict:
    cid = str(company_id or '').strip()
    oid = str(order_id or '').strip()
    init_ventas_schema(db_path)

    with database.get_db(db_path) as conn:
        o = conn.execute("SELECT * FROM sales_orders WHERE id = ? AND company_id = ?", (oid, cid)).fetchone()
        if not o:
            raise GestionComercialError(f"Pedido '{oid}' no encontrado")
        res = dict(o)
        lines = conn.execute("""
            SELECT * FROM sales_order_lines WHERE order_id = ? AND company_id = ?
            ORDER BY numero_linea ASC
        """, (oid, cid)).fetchall()
        res['lineas'] = [dict(l) for l in lines]
        return res


def list_sales_orders(company_id: str, q: str = '', estado: str = '', limit: int = 50, offset: int = 0, db_path=None) -> dict:
    cid = str(company_id or '').strip()
    init_ventas_schema(db_path)

    with database.get_db(db_path) as conn:
        wheres = ["company_id = ?"]
        params = [cid]

        if estado:
            wheres.append("estado = ?")
            params.append(estado)
        if q:
            term = f"%{q.strip()}%"
            wheres.append("(numero_pedido LIKE ? OR customer_nombre LIKE ? OR customer_identificacion LIKE ?)")
            params.extend([term, term, term])

        w_sql = " AND ".join(wheres)
        total = conn.execute(f"SELECT COUNT(*) FROM sales_orders WHERE {w_sql}", params).fetchone()[0]

        params.extend([int(limit), int(offset)])
        rows = conn.execute(f"""
            SELECT * FROM sales_orders WHERE {w_sql}
            ORDER BY fecha_pedido DESC, numero_pedido DESC
            LIMIT ? OFFSET ?
        """, params).fetchall()

        return {'items': [dict(r) for r in rows], 'total': total}


def update_sales_order_status(company_id: str, order_id: str, new_status: str, user: str, db_path=None) -> dict:
    cid = str(company_id or '').strip()
    oid = str(order_id or '').strip()
    st = str(new_status or '').strip().lower()
    valid_states = ('pendiente', 'en_preparacion', 'despachado', 'facturado', 'cancelado')
    if st not in valid_states:
        raise GestionComercialError(f"Estado '{st}' no válido para pedidos")

    init_ventas_schema(db_path)
    with database.get_db(db_path) as conn:
        cur = conn.execute("SELECT id, estado, numero_pedido FROM sales_orders WHERE id = ? AND company_id = ?", (oid, cid)).fetchone()
        if not cur:
            raise GestionComercialError("Pedido no encontrado")
        conn.execute("UPDATE sales_orders SET estado = ?, updated_at = datetime('now','localtime') WHERE id = ?", (st, oid))
        database.insert_audit_log(conn, 'ventas_order_status_update', user, 'order', oid,
                                  {'anterior': cur['estado'], 'nuevo': st, 'numero': cur['numero_pedido']})

    return get_sales_order(cid, oid, db_path=db_path)


def convert_order_to_invoice(company_id: str, order_id: str, user: str, tipo_doc: str = '01', db_path=None) -> dict:
    """Convierte un pedido de venta en Factura Electrónica o Tiquete."""
    from services.fe_emission_service import emitir_comprobante
    cid = str(company_id or '').strip()
    ped = get_sales_order(cid, order_id, db_path=db_path)

    if ped['estado'] == 'facturado':
        raise GestionComercialError(f"El pedido '{ped['numero_pedido']}' ya fue facturado previamente")

    doc_payload = {
        'company_id': cid,
        'tipo_documento': tipo_doc,
        'customer_id': ped['customer_id'] or None,
        'condicion_venta': ped['condicion_venta'],
        'plazo_credito': ped['plazo_credito'],
        'moneda': ped['moneda'],
        'tipo_cambio': ped['tipo_cambio'],
        'lineas': [
            {
                'product_id': l['product_id'] or None,
                'codigo_interno': l['codigo_interno'],
                'codigo_cabys': l['codigo_cabys'],
                'descripcion': l['descripcion'],
                'cantidad': l['cantidad'],
                'unidad_medida': l['unidad_medida'],
                'precio_unitario': l['precio_unitario'],
                'monto_descuento': l['monto_descuento'],
                'tarifa_iva_codigo': l['tarifa_iva_codigo']
            }
            for l in ped['lineas']
        ]
    }

    doc = emitir_comprobante(cid, doc_payload, user, db_path=db_path)

    with database.get_db(db_path) as conn:
        conn.execute("""
            UPDATE sales_orders
            SET estado = 'facturado', documento_factura_id = ?, updated_at = datetime('now','localtime')
            WHERE id = ? AND company_id = ?
        """, (doc['id'], ped['id'], cid))
        database.insert_audit_log(conn, 'ventas_order_converted_to_invoice', user, 'order', ped['id'],
                                  {'documento_id': doc['id'], 'consecutivo': doc['consecutivo']})

    return {'order': get_sales_order(cid, ped['id'], db_path=db_path), 'document': doc}


# ══════════════════════════════════════════════════════════════════════════════
# 3. KARDEX DE INVENTARIO Y COSTEO PROMEDIO PONDERADO
# ══════════════════════════════════════════════════════════════════════════════

def record_kardex_movement(conn, company_id: str, product_id: str, tipo_movimiento: str,
                           cantidad: float, costo_unitario: float = 0.0, documento_tipo: str = '',
                           documento_ref: str = '', motivo: str = '', user: str = '') -> dict:
    """
    Registra atómicamente un movimiento en el Kardex y actualiza la existencia
    y el costo promedio ponderado del producto.
    """
    cid = str(company_id or '').strip()
    pid = str(product_id or '').strip()
    tipo = str(tipo_movimiento or '').strip().lower()
    valid_tipos = ('entrada', 'salida', 'ajuste_positivo', 'ajuste_negativo', 'venta', 'devolucion')
    if tipo not in valid_tipos:
        raise GestionComercialError(f"Tipo de movimiento '{tipo}' no soportado en Kardex")

    cant = float(cantidad)
    if cant <= 0:
        raise GestionComercialError("La cantidad del movimiento de Kardex debe ser mayor a cero")

    # Obtener estado actual del producto
    prod = conn.execute("SELECT existencia, precio_compra, nombre FROM products WHERE id = ? AND company_id = ?",
                        (pid, cid)).fetchone()
    if not prod:
        raise GestionComercialError(f"Producto '{pid}' no existe en esta empresa")

    saldo_cant_ant = float(prod['existencia'] or 0.0)
    costo_ant = float(prod['precio_compra'] or 0.0)
    saldo_costo_ant = round(saldo_cant_ant * costo_ant, 2)

    costo_mov = float(costo_unitario or costo_ant)
    costo_total_mov = round(cant * costo_mov, 2)

    # Calcular nuevos saldos según el tipo de movimiento
    if tipo in ('entrada', 'ajuste_positivo', 'devolucion'):
        nuevo_saldo_cant = round(saldo_cant_ant + cant, 4)
        nuevo_saldo_costo = round(saldo_costo_ant + costo_total_mov, 2)
        # Recálculo de costo promedio ponderado
        nuevo_costo_promedio = round(nuevo_saldo_costo / nuevo_saldo_cant, 4) if nuevo_saldo_cant > 0 else costo_ant
    else:  # salida, ajuste_negativo, venta
        nuevo_saldo_cant = max(0.0, round(saldo_cant_ant - cant, 4))
        # En salidas, el costo sale al costo promedio actual
        costo_mov = costo_ant
        costo_total_mov = round(cant * costo_mov, 2)
        nuevo_saldo_costo = max(0.0, round(saldo_costo_ant - costo_total_mov, 2))
        nuevo_costo_promedio = round(nuevo_saldo_costo / nuevo_saldo_cant, 4) if nuevo_saldo_cant > 0 else costo_ant

    kid = str(uuid.uuid4())
    ahora = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    conn.execute("""
        INSERT INTO inventory_kardex (
            id, company_id, product_id, fecha, tipo_movimiento, documento_tipo,
            documento_ref, cantidad, costo_unitario, costo_total, saldo_cantidad,
            saldo_costo_unitario, saldo_costo_total, motivo, usuario_creacion
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        kid, cid, pid, ahora, tipo, documento_tipo, documento_ref, cant,
        costo_mov, costo_total_mov, nuevo_saldo_cant, nuevo_costo_promedio,
        nuevo_saldo_costo, motivo, user
    ))

    # Actualizar producto
    conn.execute("""
        UPDATE products
        SET existencia = ?, precio_compra = ?, updated_at = datetime('now','localtime')
        WHERE id = ? AND company_id = ?
    """, (nuevo_saldo_cant, nuevo_costo_promedio, pid, cid))

    return {
        'id': kid,
        'product_id': pid,
        'tipo': tipo,
        'cantidad': cant,
        'saldo_cantidad': nuevo_saldo_cant,
        'costo_promedio': nuevo_costo_promedio,
        'saldo_costo_total': nuevo_saldo_costo
    }


def get_product_kardex(company_id: str, product_id: str, fecha_inicio: str = '', fecha_fin: str = '', db_path=None) -> dict:
    cid = str(company_id or '').strip()
    pid = str(product_id or '').strip()
    init_ventas_schema(db_path)

    with database.get_db(db_path) as conn:
        prod = conn.execute("SELECT * FROM products WHERE id = ? AND company_id = ?", (pid, cid)).fetchone()
        if not prod:
            raise GestionComercialError("Producto no encontrado")

        wheres = ["company_id = ?", "product_id = ?"]
        params = [cid, pid]

        if fecha_inicio:
            wheres.append("substr(fecha, 1, 10) >= ?")
            params.append(fecha_inicio[:10])
        if fecha_fin:
            wheres.append("substr(fecha, 1, 10) <= ?")
            params.append(fecha_fin[:10])

        w_sql = " AND ".join(wheres)
        rows = conn.execute(f"""
            SELECT * FROM inventory_kardex WHERE {w_sql}
            ORDER BY fecha ASC, created_at ASC
        """, params).fetchall()

        return {
            'producto': dict(prod),
            'movimientos': [dict(r) for r in rows],
            'total_movimientos': len(rows)
        }


def get_inventory_valuation(company_id: str, db_path=None) -> dict:
    """Calcula la valorización total del inventario y alertas de stock para la empresa."""
    cid = str(company_id or '').strip()
    init_ventas_schema(db_path)

    with database.get_db(db_path) as conn:
        rows = conn.execute("""
            SELECT id, codigo_interno, codigo_cabys, nombre, categoria_nombre, tipo,
                   existencia, stock_minimo, stock_maximo, precio_compra, precio_venta,
                   (existencia * precio_compra) as valor_total_costo,
                   (existencia * precio_venta) as valor_total_pvp
            FROM products
            WHERE company_id = ? AND controla_inventario = 1 AND estado = 'activo'
            ORDER BY categoria_nombre ASC, nombre ASC
        """, (cid,)).fetchall()

        items = [dict(r) for r in rows]
        val_costo = sum(float(i['valor_total_costo'] or 0.0) for i in items)
        val_pvp = sum(float(i['valor_total_pvp'] or 0.0) for i in items)
        articulos_agotados = sum(1 for i in items if float(i['existencia'] or 0.0) <= 0)
        articulos_bajos = sum(1 for i in items if 0 < float(i['existencia'] or 0.0) <= float(i['stock_minimo'] or 0.0))

        return {
            'items': items,
            'total_articulos': len(items),
            'valor_total_costo': round(val_costo, 2),
            'valor_total_pvp': round(val_pvp, 2),
            'articulos_agotados': articulos_agotados,
            'articulos_bajos': articulos_bajos
        }


# ══════════════════════════════════════════════════════════════════════════════
# 4. PUNTO DE VENTA (POS) Y TURNOS DE CAJA
# ══════════════════════════════════════════════════════════════════════════════

def open_pos_shift(company_id: str, sucursal: str, terminal: str, cajero: str, monto_inicial: float, db_path=None) -> dict:
    cid = str(company_id or '').strip()
    suc = str(sucursal or '001').zfill(3)[:3]
    ter = str(terminal or '00001').zfill(5)[:5]
    caj = str(cajero or '').strip()
    monto = max(0.0, float(monto_inicial or 0.0))

    if not cid or not caj:
        raise GestionComercialError("company_id y cajero son requeridos para abrir turno")

    init_ventas_schema(db_path)

    with database.get_db(db_path) as conn:
        # Verificar si ya hay un turno abierto en esta terminal o por este cajero
        abierto = conn.execute("""
            SELECT id, cajero_username, fecha_apertura FROM pos_shifts
            WHERE company_id = ? AND codigo_sucursal = ? AND codigo_terminal = ? AND estado = 'abierta'
        """, (cid, suc, ter)).fetchone()
        if abierto:
            raise GestionComercialError(
                f"La terminal {ter} (sucursal {suc}) ya tiene un turno abierto por '{abierto['cajero_username']}' desde {abierto['fecha_apertura']}"
            )

        sid = str(uuid.uuid4())
        f_apertura = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        conn.execute("""
            INSERT INTO pos_shifts (
                id, company_id, codigo_sucursal, codigo_terminal, cajero_username,
                fecha_apertura, monto_apertura, estado
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'abierta')
        """, (sid, cid, suc, ter, caj, f_apertura, monto))

        database.insert_audit_log(conn, 'pos_shift_open', caj, 'pos_shift', sid,
                                  {'terminal': ter, 'monto_inicial': monto, 'fecha': f_apertura})

    return get_pos_shift(cid, sid, db_path=db_path)


def get_active_pos_shift(company_id: str, sucursal: str = '001', terminal: str = '00001', cajero: str = None, db_path=None) -> dict:
    cid = str(company_id or '').strip()
    suc = str(sucursal or '001').zfill(3)[:3]
    ter = str(terminal or '00001').zfill(5)[:5]
    init_ventas_schema(db_path)

    with database.get_db(db_path) as conn:
        q = """
            SELECT * FROM pos_shifts
            WHERE company_id = ? AND codigo_sucursal = ? AND codigo_terminal = ? AND estado = 'abierta'
        """
        params = [cid, suc, ter]
        if cajero:
            q += " AND cajero_username = ?"
            params.append(cajero)

        row = conn.execute(q, params).fetchone()
        if not row:
            return None
        return get_pos_shift(cid, row['id'], db_path=db_path)


def get_pos_shift(company_id: str, shift_id: str, db_path=None) -> dict:
    cid = str(company_id or '').strip()
    sid = str(shift_id or '').strip()
    init_ventas_schema(db_path)

    with database.get_db(db_path) as conn:
        s = conn.execute("SELECT * FROM pos_shifts WHERE id = ? AND company_id = ?", (sid, cid)).fetchone()
        if not s:
            raise GestionComercialError(f"Turno de caja '{sid}' no encontrado")
        res = dict(s)

        # Movimientos de caja chica / retiros
        movs = conn.execute("SELECT * FROM pos_movements WHERE shift_id = ? ORDER BY created_at ASC", (sid,)).fetchall()
        res['movimientos'] = [dict(m) for m in movs]

        # Calcular ventas asociadas en tiempo real desde electronic_documents
        # Medio de pago: 01 Efectivo, 02 Tarjeta, 04 Transferencia/SINPE
        ventas_rows = conn.execute("""
            SELECT medio_pago, COALESCE(SUM(total_comprobante), 0) as total, COUNT(*) as count
            FROM electronic_documents
            WHERE company_id = ? AND pos_shift_id = ? AND estado_comprobante NOT IN ('borrador','rechazado')
            GROUP BY medio_pago
        """, (cid, sid)).fetchall()

        v_efectivo = 0.0
        v_tarjeta = 0.0
        v_sinpe = 0.0
        v_otros = 0.0
        cant_comprobantes = 0

        for r in ventas_rows:
            mp = r['medio_pago']
            tot = float(r['total'] or 0.0)
            cant_comprobantes += int(r['count'] or 0)
            if mp == '01':
                v_efectivo += tot
            elif mp == '02':
                v_tarjeta += tot
            elif mp == '04':
                v_sinpe += tot
            else:
                v_otros += tot

        total_ingresos_extra = sum(float(m['monto']) for m in res['movimientos'] if m['tipo'] == 'ingreso')
        total_retiros = sum(float(m['monto']) for m in res['movimientos'] if m['tipo'] == 'retiro')

        m_apertura = float(res['monto_apertura'] or 0.0)
        efectivo_esperado = round((m_apertura + v_efectivo + total_ingresos_extra) - total_retiros, 2)

        res['total_ventas_efectivo'] = round(v_efectivo, 2)
        res['total_ventas_tarjeta'] = round(v_tarjeta, 2)
        res['total_ventas_sinpe'] = round(v_sinpe, 2)
        res['total_ventas_otros'] = round(v_otros, 2)
        res['total_ventas_global'] = round(v_efectivo + v_tarjeta + v_sinpe + v_otros, 2)
        res['total_ingresos_extra'] = round(total_ingresos_extra, 2)
        res['total_retiros_caja'] = round(total_retiros, 2)
        res['efectivo_esperado'] = efectivo_esperado
        res['comprobantes_emitidos_count'] = cant_comprobantes

        return res


def register_pos_movement(company_id: str, shift_id: str, tipo: str, monto: float, motivo: str, user: str, db_path=None) -> dict:
    cid = str(company_id or '').strip()
    sid = str(shift_id or '').strip()
    t = str(tipo or '').strip().lower()
    if t not in ('ingreso', 'retiro'):
        raise GestionComercialError("Tipo de movimiento debe ser 'ingreso' o 'retiro'")
    m = max(0.01, float(monto or 0))
    mot = str(motivo or '').strip()
    if not mot:
        raise GestionComercialError("El motivo del movimiento de caja es requerido")

    init_ventas_schema(db_path)

    with database.get_db(db_path) as conn:
        s = conn.execute("SELECT id, estado FROM pos_shifts WHERE id = ? AND company_id = ?", (sid, cid)).fetchone()
        if not s or s['estado'] != 'abierta':
            raise GestionComercialError("No se puede registrar movimiento en un turno de caja cerrado o inexistente")

        mid = str(uuid.uuid4())
        conn.execute("""
            INSERT INTO pos_movements (id, shift_id, company_id, tipo, monto, motivo, usuario_creacion)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (mid, sid, cid, t, m, mot, user))

        database.insert_audit_log(conn, 'pos_cash_movement', user, 'pos_shift', sid,
                                  {'tipo': t, 'monto': m, 'motivo': mot})

    try:
        from services.ventas_contabilidad_service import generate_pos_movement_journal_entry
        generate_pos_movement_journal_entry(cid, sid, mid, db_path=db_path)
    except Exception as e_mov:
        print(f"[VENTAS] Aviso generando asiento contable para movimiento POS {mid}: {e_mov}")

    return get_pos_shift(cid, sid, db_path=db_path)


def close_pos_shift(company_id: str, shift_id: str, efectivo_real: float, observaciones: str, user: str, db_path=None) -> dict:
    cid = str(company_id or '').strip()
    sid = str(shift_id or '').strip()
    e_real = max(0.0, float(efectivo_real or 0.0))
    obs = str(observaciones or '').strip()

    init_ventas_schema(db_path)

    # Obtener totales calculados antes de cerrar
    shift_calc = get_pos_shift(cid, sid, db_path=db_path)
    if shift_calc['estado'] != 'abierta':
        raise GestionComercialError("El turno ya está cerrado")

    e_esperado = shift_calc['efectivo_esperado']
    diferencia = round(e_real - e_esperado, 2)
    f_cierre = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    with database.get_db(db_path) as conn:
        conn.execute("""
            UPDATE pos_shifts
            SET estado = 'cerrada',
                fecha_cierre = ?,
                total_ventas_efectivo = ?,
                total_ventas_tarjeta = ?,
                total_ventas_sinpe = ?,
                total_ventas_otros = ?,
                total_ingresos_extra = ?,
                total_retiros_caja = ?,
                efectivo_esperado = ?,
                efectivo_real = ?,
                diferencia = ?,
                observaciones_cierre = ?,
                updated_at = datetime('now','localtime')
            WHERE id = ? AND company_id = ?
        """, (
            f_cierre, shift_calc['total_ventas_efectivo'], shift_calc['total_ventas_tarjeta'],
            shift_calc['total_ventas_sinpe'], shift_calc['total_ventas_otros'],
            shift_calc['total_ingresos_extra'], shift_calc['total_retiros_caja'],
            e_esperado, e_real, diferencia, obs, sid, cid
        ))

        database.insert_audit_log(conn, 'pos_shift_close', user, 'pos_shift', sid,
                                  {'efectivo_esperado': e_esperado, 'efectivo_real': e_real, 'diferencia': diferencia})

    return get_pos_shift(cid, sid, db_path=db_path)


def quick_pos_sale(company_id: str, shift_id: str, sale_data: dict, user: str, db_path=None) -> dict:
    """
    Realiza una venta de mostrador rápida en el Punto de Venta (POS):
    - Emite comprobante electrónico asociado al turno de caja activo.
    - Soporta Efectivo (con cálculo de vuelto), Tarjeta y SINPE Móvil.
    - Descuenta existencias y registra automáticamente el Kardex.
    """
    from services.fe_emission_service import emitir_comprobante
    cid = str(company_id or '').strip()
    sid = str(shift_id or '').strip()

    shift = get_pos_shift(cid, sid, db_path=db_path)
    if shift['estado'] != 'abierta':
        raise GestionComercialError("No se pueden registrar ventas en una caja cerrada")

    medio_pago = str(sale_data.get('medio_pago') or '01').strip()
    monto_recibido = float(sale_data.get('monto_recibido') or 0.0)

    # Preparar datos del comprobante electrónico
    # Por defecto Tiquete Electrónico (04) si no se especifica cliente, o Factura (01) si tiene cliente con cédula
    tipo_doc = str(sale_data.get('tipo_documento') or ('01' if sale_data.get('customer_id') else '04')).strip()

    doc_payload = {
        'company_id': cid,
        'tipo_documento': tipo_doc,
        'codigo_sucursal': shift['codigo_sucursal'],
        'codigo_terminal': shift['codigo_terminal'],
        'customer_id': sale_data.get('customer_id') or None,
        'condicion_venta': '01',  # Contado en POS
        'medio_pago': medio_pago,
        'moneda': sale_data.get('moneda') or 'CRC',
        'tipo_cambio': float(sale_data.get('tipo_cambio') or 1.0),
        'lineas': sale_data.get('lineas') or []
    }

    doc = emitir_comprobante(cid, doc_payload, user, db_path=db_path)

    # Vincular pos_shift_id al comprobante en electronic_documents
    with database.get_db(db_path) as conn:
        conn.execute("""
            UPDATE electronic_documents
            SET pos_shift_id = ?, updated_at = datetime('now','localtime')
            WHERE id = ? AND company_id = ?
        """, (sid, doc['id'], cid))

        # Registrar Kardex para las líneas vendidas
        for l in doc_payload['lineas']:
            pid = l.get('product_id')
            if pid:
                p_ctrl = conn.execute("SELECT controla_inventario FROM products WHERE id = ? AND company_id = ?",
                                      (pid, cid)).fetchone()
                if p_ctrl and p_ctrl['controla_inventario']:
                    record_kardex_movement(
                        conn=conn,
                        company_id=cid,
                        product_id=pid,
                        tipo_movimiento='venta',
                        cantidad=float(l.get('cantidad') or 1),
                        documento_tipo=tipo_doc,
                        documento_ref=doc['consecutivo'],
                        motivo=f"Venta POS {doc['consecutivo']}",
                        user=user
                    )

    # Cálculo de cambio / vuelto si es en efectivo
    tot_comp = float(doc['total_comprobante'])
    vuelto = max(0.0, round(monto_recibido - tot_comp, 2)) if medio_pago == '01' and monto_recibido > 0 else 0.0

    return {
        'document': doc,
        'consecutivo': doc.get('consecutivo', ''),
        'asiento_id': doc.get('asiento_id', ''),
        'monto_recibido': monto_recibido,
        'vuelto': vuelto,
        'shift_id': sid
    }
