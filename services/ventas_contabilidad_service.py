"""
FACTURAMI - Servicio de Integración Contable con NIIF y Asientos Automáticos (Fase 7)
=====================================================================================
Generación automática de asientos en el libro diario para:
  1. Ventas de contado (Efectivo, Tarjeta, SINPE) y ventas a crédito (CxC).
  2. Desglose de Ingresos Gravados, Ingresos Exentos y Débito Fiscal IVA.
  3. Costo de Ventas e Inventario a costo promedio ponderado.
  4. Notas de Crédito (anulación / devolución comercial e inventario).
  5. Pagos y abonos de Cuentas por Cobrar (CxC).
  6. Mapeo contable configurable por empresa con catálogo estándar NIIF.
"""
import uuid
from datetime import date, datetime
from typing import Dict, Any, List, Optional
import db as database


DEFAULT_COMPANY_ACCOUNTING_ACCOUNTS = [
    {
        'codigo': '1.1.1.01.01',
        'nombre': 'Caja General y Fondos Fijos (POS)',
        'tipo': 'activo',
        'naturaleza': 'D',
        'nivel': 4
    },
    {
        'codigo': '1.1.1.02.01',
        'nombre': 'Bancos Cuentas Corrientes y SINPE Móvil',
        'tipo': 'activo',
        'naturaleza': 'D',
        'nivel': 4
    },
    {
        'codigo': '1.1.1.02.02',
        'nombre': 'Bancos Terminales Punto de Venta (Tarjetas)',
        'tipo': 'activo',
        'naturaleza': 'D',
        'nivel': 4
    },
    {
        'codigo': '1.1.2.01',
        'nombre': 'Cuentas por Cobrar Comerciales - Clientes',
        'tipo': 'activo',
        'naturaleza': 'D',
        'nivel': 3
    },
    {
        'codigo': '1.1.3.01',
        'nombre': 'Inventario de Mercaderías para la Venta',
        'tipo': 'activo',
        'naturaleza': 'D',
        'nivel': 3
    },
    {
        'codigo': '2.1.3.01',
        'nombre': 'IVA por Pagar - Débito Fiscal Ventas',
        'tipo': 'pasivo',
        'naturaleza': 'A',
        'nivel': 3
    },
    {
        'codigo': '4.1.1.01',
        'nombre': 'Ingresos por Ventas de Bienes y Servicios Gravados',
        'tipo': 'ingreso',
        'naturaleza': 'A',
        'nivel': 3
    },
    {
        'codigo': '4.1.1.02',
        'nombre': 'Ingresos por Ventas Exentas y no Sujetas',
        'tipo': 'ingreso',
        'naturaleza': 'A',
        'nivel': 3
    },
    {
        'codigo': '4.1.2.01',
        'nombre': 'Devoluciones y Descuentos Concedidos sobre Ventas',
        'tipo': 'ingreso',
        'naturaleza': 'D',
        'nivel': 3
    },
    {
        'codigo': '5.1.1.01',
        'nombre': 'Costo de Ventas de Mercaderías',
        'tipo': 'costo',
        'naturaleza': 'D',
        'nivel': 3
    }
]


def ensure_company_chart_of_accounts(company_id: str, db_path=None) -> None:
    """
    Garantiza que el catálogo de cuentas de la empresa cuente con las cuentas estándar NIIF.
    Si ya existen, no las modifica ni sobrescribe.
    """
    cid = str(company_id or '').strip()
    if not cid:
        return

    with database.get_db(db_path) as conn:
        for acc in DEFAULT_COMPANY_ACCOUNTING_ACCOUNTS:
            conn.execute("""
                INSERT OR IGNORE INTO catalogo_cuentas 
                (codigo, cliente_id, nombre, tipo, naturaleza, nivel, permite_movimiento)
                VALUES (?, ?, ?, ?, ?, ?, 1)
            """, (
                acc['codigo'], cid, acc['nombre'],
                acc['tipo'], acc['naturaleza'], acc['nivel']
            ))


def get_or_create_accounting_mapping(company_id: str, db_path=None) -> Dict[str, Any]:
    """
    Obtiene la configuración de mapeo contable de la empresa.
    Si no existe aún, la crea con los valores estándar NIIF.
    """
    cid = str(company_id or '').strip()
    if not cid:
        raise ValueError("company_id es requerido")

    ensure_company_chart_of_accounts(cid, db_path)

    with database.get_db(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM company_accounting_mapping WHERE company_id = ?",
            (cid,)
        ).fetchone()

        if not row:
            conn.execute("""
                INSERT INTO company_accounting_mapping 
                (company_id, cuenta_caja_efectivo, cuenta_bancos_tarjeta, cuenta_bancos_transferencia,
                 cuenta_cxc_clientes, cuenta_inventario, cuenta_iva_por_pagar,
                 cuenta_ventas_ingresos, cuenta_ventas_exentas, cuenta_devoluciones_ventas,
                 cuenta_costo_ventas, auto_generar_asientos)
                VALUES (?, '1.1.1.01.01', '1.1.1.02.02', '1.1.1.02.01',
                        '1.1.2.01', '1.1.3.01', '2.1.3.01',
                        '4.1.1.01', '4.1.1.02', '4.1.2.01',
                        '5.1.1.01', 1)
            """, (cid,))
            row = conn.execute(
                "SELECT * FROM company_accounting_mapping WHERE company_id = ?",
                (cid,)
            ).fetchone()

        return dict(row)


def update_accounting_mapping(company_id: str, mapping_data: Dict[str, Any], db_path=None) -> Dict[str, Any]:
    """
    Actualiza el mapeo de cuentas contables de una empresa.
    """
    cid = str(company_id or '').strip()
    if not cid:
        raise ValueError("company_id es requerido")

    allowed_keys = {
        'cuenta_caja_efectivo', 'cuenta_bancos_tarjeta', 'cuenta_bancos_transferencia',
        'cuenta_cxc_clientes', 'cuenta_inventario', 'cuenta_iva_por_pagar',
        'cuenta_ventas_ingresos', 'cuenta_ventas_exentas', 'cuenta_devoluciones_ventas',
        'cuenta_costo_ventas', 'auto_generar_asientos'
    }

    current = get_or_create_accounting_mapping(cid, db_path)
    updates = {}
    for k, v in mapping_data.items():
        if k in allowed_keys:
            if k == 'auto_generar_asientos':
                updates[k] = 1 if bool(v) else 0
            else:
                updates[k] = str(v or '').strip() or current.get(k, '')

    if updates:
        set_clauses = [f"{k} = ?" for k in updates.keys()]
        set_clauses.append("updated_at = datetime('now','localtime')")
        vals = list(updates.values()) + [cid]

        with database.get_db(db_path) as conn:
            conn.execute(
                f"UPDATE company_accounting_mapping SET {', '.join(set_clauses)} WHERE company_id = ?",
                vals
            )

    return get_or_create_accounting_mapping(cid, db_path)


def _get_account_name(conn, company_id: str, code: str) -> str:
    """Busca el nombre de la cuenta en el catálogo o retorna fallback descriptivo."""
    r = conn.execute(
        "SELECT nombre FROM catalogo_cuentas WHERE cliente_id = ? AND codigo = ?",
        (company_id, code)
    ).fetchone()
    if r and r['nombre']:
        return r['nombre']
    for acc in DEFAULT_COMPANY_ACCOUNTING_ACCOUNTS:
        if acc['codigo'] == code:
            return acc['nombre']
    return f"Cuenta {code}"


def generate_invoice_journal_entry(company_id: str, doc_id_or_data: Any, force: bool = False, db_path=None) -> Optional[Dict[str, Any]]:
    """
    Genera el asiento contable NIIF para una Factura (01) o Tiquete Electrónico (04):
      - Débito: Caja, Bancos o CxC según condición de venta y medio de pago.
      - Crédito: Ingresos por Ventas Gravadas / Exentas.
      - Crédito: IVA por Pagar (Débito Fiscal).
      - Asiento adicional: Costo de Ventas e Inventario para artículos de inventario.
    Cumple con la estricta partida doble sum(Debe) == sum(Haber).
    """
    cid = str(company_id or '').strip()
    if not cid:
        raise ValueError("company_id es requerido")

    with database.get_db(db_path) as conn:
        # Cargar documento
        if isinstance(doc_id_or_data, dict):
            doc = doc_id_or_data
            doc_id = doc.get('id')
        else:
            doc_id = str(doc_id_or_data)
            r = conn.execute(
                "SELECT * FROM electronic_documents WHERE id = ? AND company_id = ?",
                (doc_id, cid)
            ).fetchone()
            if not r:
                return None
            doc = dict(r)

        # Si ya tiene asiento asignado, devolverlo
        if doc.get('asiento_id'):
            return {
                'asiento_id': doc['asiento_id'],
                'asiento_costo_id': doc.get('asiento_costo_id', ''),
                'existente': True
            }

        # Cargar mapeo
        mapping = get_or_create_accounting_mapping(cid, db_path)
        if not force and not mapping.get('auto_generar_asientos', 1):
            return None

        # Cargar líneas
        lines_rows = conn.execute(
            "SELECT * FROM electronic_document_lines WHERE document_id = ? AND company_id = ? ORDER BY numero_linea ASC",
            (doc_id, cid)
        ).fetchall()
        lines = [dict(l) for l in lines_rows]

        condicion = doc.get('condicion_venta', '01')
        medio_pago = doc.get('medio_pago', '01')
        total_comprobante = round(float(doc.get('total_comprobante', 0) or 0), 2)
        total_impuesto = round(float(doc.get('total_impuestos', doc.get('total_impuesto', 0)) or 0), 2)
        total_venta_neta = round(float(doc.get('total_venta_neta', doc.get('subtotal', 0)) or 0), 2)
        consecutivo = doc.get('consecutivo', '')
        clave = doc.get('clave_numerica', '')
        fecha_emision = (doc.get('fecha_emision') or date.today().isoformat())[:10]
        cliente_nombre = doc.get('receptor_nombre', 'Cliente General')

        if total_comprobante <= 0:
            return None

        # 1. Determinar Cuenta Deudora (Débito)
        es_credito = condicion in ('02', '03', '06') or int(doc.get('plazo_credito', 0) or 0) > 0
        if es_credito:
            cta_debito = mapping.get('cuenta_cxc_clientes') or '1.1.2.01'
        else:
            if medio_pago == '02': # Tarjeta
                cta_debito = mapping.get('cuenta_bancos_tarjeta') or '1.1.1.02.02'
            elif medio_pago in ('04', '03'): # Transferencia / SINPE / Cheque
                cta_debito = mapping.get('cuenta_bancos_transferencia') or '1.1.1.02.01'
            else: # Efectivo '01' o default
                cta_debito = mapping.get('cuenta_caja_efectivo') or '1.1.1.01.01'

        nom_debito = _get_account_name(conn, cid, cta_debito)

        # 2. Desglosar Ventas Gravadas y Exentas
        subtotal_gravado = 0.0
        subtotal_exento = 0.0
        for l in lines:
            tarifa = float(l.get('tarifa_iva_porcentaje', l.get('tarifa_impuesto', 0)) or 0)
            sub = float(l.get('subtotal_linea', l.get('subtotal', 0)) or 0)
            if tarifa > 0:
                subtotal_gravado += sub
            else:
                subtotal_exento += sub

        subtotal_gravado = round(subtotal_gravado, 2)
        subtotal_exento = round(subtotal_exento, 2)
        if subtotal_gravado + subtotal_exento == 0:
            subtotal_gravado = total_venta_neta

        cta_ing_gravado = mapping.get('cuenta_ventas_ingresos') or '4.1.1.01'
        nom_ing_gravado = _get_account_name(conn, cid, cta_ing_gravado)

        cta_ing_exento = mapping.get('cuenta_ventas_exentas') or '4.1.1.02'
        nom_ing_exento = _get_account_name(conn, cid, cta_ing_exento)

        cta_iva = mapping.get('cuenta_iva_por_pagar') or '2.1.3.01'
        nom_iva = _get_account_name(conn, cid, cta_iva)

        # Construir líneas del asiento de venta
        asiento_lineas = []
        # Línea 1: Débito por el total
        asiento_lineas.append({
            'cuenta_codigo': cta_debito,
            'cuenta_nombre': nom_debito,
            'debito': total_comprobante,
            'credito': 0.0,
            'referencia_doc': consecutivo
        })

        # Líneas de Crédito
        if subtotal_gravado > 0:
            asiento_lineas.append({
                'cuenta_codigo': cta_ing_gravado,
                'cuenta_nombre': nom_ing_gravado,
                'debito': 0.0,
                'credito': subtotal_gravado,
                'referencia_doc': consecutivo
            })
        if subtotal_exento > 0:
            asiento_lineas.append({
                'cuenta_codigo': cta_ing_exento,
                'cuenta_nombre': nom_ing_exento,
                'debito': 0.0,
                'credito': subtotal_exento,
                'referencia_doc': consecutivo
            })
        if total_impuesto > 0:
            asiento_lineas.append({
                'cuenta_codigo': cta_iva,
                'cuenta_nombre': nom_iva,
                'debito': 0.0,
                'credito': total_impuesto,
                'referencia_doc': consecutivo
            })

        # Ajuste de redondeo de centavos para estricta partida doble
        tot_deb = round(sum(l['debito'] for l in asiento_lineas), 2)
        tot_cre = round(sum(l['credito'] for l in asiento_lineas), 2)
        dif = round(tot_deb - tot_cre, 2)
        if abs(dif) > 0 and abs(dif) <= 0.05:
            for l in asiento_lineas:
                if l['credito'] > 0:
                    l['credito'] = round(l['credito'] + dif, 2)
                    break

        tipo_doc_desc = 'Factura' if doc.get('tipo_documento') == '01' else 'Tiquete'
        concepto_venta = f"Venta {tipo_doc_desc} {consecutivo} - {cliente_nombre} (Clave: {clave[:20]}...)"
        asiento_dict = {
            'cliente_id': cid,
            'fecha_contable': fecha_emision,
            'tipo_operacion': 'venta_credito' if es_credito else 'venta_contado',
            'documento_origen': consecutivo,
            'modulo_origen': 'ventas',
            'concepto': concepto_venta,
            'hash_origen': clave
        }

        asiento_id = database.insert_asiento(conn, asiento_dict, asiento_lineas)
        conn.execute(
            "UPDATE electronic_documents SET asiento_id = ? WHERE id = ? AND company_id = ?",
            (asiento_id, doc_id, cid)
        )

        # 3. Asiento de Costo de Ventas e Inventario (si aplica)
        asiento_costo_id = ''
        costo_total = 0.0
        for l in lines:
            pid = l.get('product_id')
            qty = float(l.get('cantidad', 0) or 0)
            if pid and qty > 0:
                p_row = conn.execute(
                    "SELECT controla_inventario, precio_compra FROM products WHERE id = ? AND company_id = ?",
                    (pid, cid)
                ).fetchone()
                if p_row and p_row['controla_inventario']:
                    kdx = conn.execute(
                        "SELECT saldo_costo_unitario FROM inventory_kardex WHERE product_id = ? AND company_id = ? ORDER BY fecha DESC, id DESC LIMIT 1",
                        (pid, cid)
                    ).fetchone()
                    cpp = float(kdx['saldo_costo_unitario']) if kdx and float(kdx['saldo_costo_unitario'] or 0) > 0 else float(p_row['precio_compra'] or 0)
                    if cpp > 0:
                        costo_total += (qty * cpp)

        costo_total = round(costo_total, 2)
        if costo_total > 0:
            cta_costo = mapping.get('cuenta_costo_ventas') or '5.1.1.01'
            nom_costo = _get_account_name(conn, cid, cta_costo)
            cta_inv = mapping.get('cuenta_inventario') or '1.1.3.01'
            nom_inv = _get_account_name(conn, cid, cta_inv)

            lineas_costo = [
                {
                    'cuenta_codigo': cta_costo,
                    'cuenta_nombre': nom_costo,
                    'debito': costo_total,
                    'credito': 0.0,
                    'referencia_doc': consecutivo
                },
                {
                    'cuenta_codigo': cta_inv,
                    'cuenta_nombre': nom_inv,
                    'debito': 0.0,
                    'credito': costo_total,
                    'referencia_doc': consecutivo
                }
            ]
            asiento_costo_dict = {
                'cliente_id': cid,
                'fecha_contable': fecha_emision,
                'tipo_operacion': 'costo_venta',
                'documento_origen': f"CV-{consecutivo}",
                'modulo_origen': 'ventas',
                'concepto': f"Costo de ventas e inventario - Comprobante {consecutivo}",
                'hash_origen': clave
            }
            asiento_costo_id = database.insert_asiento(conn, asiento_costo_dict, lineas_costo)
            conn.execute(
                "UPDATE electronic_documents SET asiento_costo_id = ? WHERE id = ? AND company_id = ?",
                (asiento_costo_id, doc_id, cid)
            )

        return {
            'asiento_id': asiento_id,
            'asiento_costo_id': asiento_costo_id,
            'total_debe': total_comprobante,
            'costo_total': costo_total
        }


def generate_credit_note_journal_entry(company_id: str, doc_id_or_data: Any, force: bool = False, db_path=None) -> Optional[Dict[str, Any]]:
    """
    Genera el asiento contable NIIF para una Nota de Crédito (03):
      - Débito: Devoluciones / Descuentos sobre Ventas (o reversión de Ingresos).
      - Débito: IVA por Pagar (reversión de Débito Fiscal).
      - Crédito: CxC Clientes o Caja/Bancos.
      - Asiento adicional de reversión de costo de inventario si hubo retorno de mercaderías.
    """
    cid = str(company_id or '').strip()
    if not cid:
        raise ValueError("company_id es requerido")

    with database.get_db(db_path) as conn:
        if isinstance(doc_id_or_data, dict):
            doc = doc_id_or_data
            doc_id = doc.get('id')
        else:
            doc_id = str(doc_id_or_data)
            r = conn.execute(
                "SELECT * FROM electronic_documents WHERE id = ? AND company_id = ?",
                (doc_id, cid)
            ).fetchone()
            if not r:
                return None
            doc = dict(r)

        if doc.get('asiento_id'):
            return {'asiento_id': doc['asiento_id'], 'existente': True}

        mapping = get_or_create_accounting_mapping(cid, db_path)
        if not force and not mapping.get('auto_generar_asientos', 1):
            return None

        lines_rows = conn.execute(
            "SELECT * FROM electronic_document_lines WHERE document_id = ? AND company_id = ? ORDER BY numero_linea ASC",
            (doc_id, cid)
        ).fetchall()
        lines = [dict(l) for l in lines_rows]

        total_comprobante = round(float(doc.get('total_comprobante', 0) or 0), 2)
        total_impuesto = round(float(doc.get('total_impuestos', doc.get('total_impuesto', 0)) or 0), 2)
        total_venta_neta = round(float(doc.get('total_venta_neta', doc.get('subtotal', 0)) or 0), 2)
        if total_venta_neta == 0 and total_comprobante > 0:
            total_venta_neta = round(total_comprobante - total_impuesto, 2)
        consecutivo = doc.get('consecutivo', '')
        clave = doc.get('clave_numerica', '')
        fecha_emision = (doc.get('fecha_emision') or date.today().isoformat())[:10]
        cliente_nombre = doc.get('receptor_nombre', 'Cliente General')
        ref_doc = doc.get('ref_numero_clave') or doc.get('referencia_consecutivo') or ''

        if total_comprobante <= 0:
            return None

        # Cuentas
        cta_dev = mapping.get('cuenta_devoluciones_ventas') or '4.1.2.01'
        nom_dev = _get_account_name(conn, cid, cta_dev)
        cta_iva = mapping.get('cuenta_iva_por_pagar') or '2.1.3.01'
        nom_iva = _get_account_name(conn, cid, cta_iva)

        # Crédito a CxC o Bancos/Caja
        condicion = doc.get('condicion_venta', '01')
        es_credito = condicion in ('02', '03')
        cta_credito = mapping.get('cuenta_cxc_clientes' if es_credito else 'cuenta_caja_efectivo') or ('1.1.2.01' if es_credito else '1.1.1.01.01')
        nom_credito = _get_account_name(conn, cid, cta_credito)

        asiento_lineas = [
            {
                'cuenta_codigo': cta_dev,
                'cuenta_nombre': nom_dev,
                'debito': total_venta_neta,
                'credito': 0.0,
                'referencia_doc': consecutivo
            }
        ]
        if total_impuesto > 0:
            asiento_lineas.append({
                'cuenta_codigo': cta_iva,
                'cuenta_nombre': nom_iva,
                'debito': total_impuesto,
                'credito': 0.0,
                'referencia_doc': consecutivo
            })
        asiento_lineas.append({
            'cuenta_codigo': cta_credito,
            'cuenta_nombre': nom_credito,
            'debito': 0.0,
            'credito': total_comprobante,
            'referencia_doc': consecutivo
        })

        # Cuadre por redondeos
        tot_deb = round(sum(l['debito'] for l in asiento_lineas), 2)
        tot_cre = round(sum(l['credito'] for l in asiento_lineas), 2)
        dif = round(tot_cre - tot_deb, 2)
        if abs(dif) > 0 and abs(dif) <= 0.05:
            asiento_lineas[0]['debito'] = round(asiento_lineas[0]['debito'] + dif, 2)

        asiento_dict = {
            'cliente_id': cid,
            'fecha_contable': fecha_emision,
            'tipo_operacion': 'nota_credito',
            'documento_origen': consecutivo,
            'modulo_origen': 'ventas',
            'concepto': f"Nota de Crédito {consecutivo} anulación/ajuste {ref_doc} - {cliente_nombre}",
            'hash_origen': clave
        }
        asiento_id = database.insert_asiento(conn, asiento_dict, asiento_lineas)
        conn.execute(
            "UPDATE electronic_documents SET asiento_id = ? WHERE id = ? AND company_id = ?",
            (asiento_id, doc_id, cid)
        )

        # Reversión de inventario y costo de ventas (si hubo reingreso)
        asiento_costo_id = ''
        costo_total = 0.0
        for l in lines:
            pid = l.get('product_id')
            qty = float(l.get('cantidad', 0) or 0)
            if pid and qty > 0:
                p_row = conn.execute(
                    "SELECT controla_inventario, precio_compra FROM products WHERE id = ? AND company_id = ?",
                    (pid, cid)
                ).fetchone()
                if p_row and p_row['controla_inventario']:
                    kdx = conn.execute(
                        "SELECT saldo_costo_unitario FROM inventory_kardex WHERE product_id = ? AND company_id = ? ORDER BY fecha DESC, id DESC LIMIT 1",
                        (pid, cid)
                    ).fetchone()
                    cpp = float(kdx['saldo_costo_unitario']) if kdx and float(kdx['saldo_costo_unitario'] or 0) > 0 else float(p_row['precio_compra'] or 0)
                    if cpp > 0:
                        costo_total += (qty * cpp)

        costo_total = round(costo_total, 2)
        if costo_total > 0:
            cta_inv = mapping.get('cuenta_inventario') or '1.1.3.01'
            nom_inv = _get_account_name(conn, cid, cta_inv)
            cta_costo = mapping.get('cuenta_costo_ventas') or '5.1.1.01'
            nom_costo = _get_account_name(conn, cid, cta_costo)

            lineas_costo = [
                {
                    'cuenta_codigo': cta_inv,
                    'cuenta_nombre': nom_inv,
                    'debito': costo_total,
                    'credito': 0.0,
                    'referencia_doc': consecutivo
                },
                {
                    'cuenta_codigo': cta_costo,
                    'cuenta_nombre': nom_costo,
                    'debito': 0.0,
                    'credito': costo_total,
                    'referencia_doc': consecutivo
                }
            ]
            asiento_costo_dict = {
                'cliente_id': cid,
                'fecha_contable': fecha_emision,
                'tipo_operacion': 'costo_venta_reversion',
                'documento_origen': f"REV-CV-{consecutivo}",
                'modulo_origen': 'ventas',
                'concepto': f"Reversión de costo e inventario por Nota de Crédito {consecutivo}",
                'hash_origen': clave
            }
            asiento_costo_id = database.insert_asiento(conn, asiento_costo_dict, lineas_costo)
            conn.execute(
                "UPDATE electronic_documents SET asiento_costo_id = ? WHERE id = ? AND company_id = ?",
                (asiento_costo_id, doc_id, cid)
            )

        return {
            'asiento_id': asiento_id,
            'asiento_costo_id': asiento_costo_id,
            'total_debe': total_comprobante
        }


def generate_ar_payment_journal_entry(
    company_id: str,
    ar_id: str,
    monto_abono: float,
    medio_pago: str = '01',
    referencia: str = '',
    db_path=None
) -> Optional[str]:
    """
    Genera asiento contable para abono o cancelación de Cuenta por Cobrar:
      - Débito: Caja General ('01'), Bancos Tarjeta ('02') o Bancos Transferencia/SINPE ('04').
      - Crédito: Cuentas por Cobrar Comerciales - Clientes.
    """
    cid = str(company_id or '').strip()
    monto = round(float(monto_abono or 0), 2)
    if not cid or not ar_id or monto <= 0:
        return None

    with database.get_db(db_path) as conn:
        ar_row = conn.execute(
            "SELECT * FROM accounts_receivable WHERE id = ? AND company_id = ?",
            (ar_id, cid)
        ).fetchone()
        if not ar_row:
            return None
        ar = dict(ar_row)

        mapping = get_or_create_accounting_mapping(cid, db_path)
        if not mapping.get('auto_generar_asientos', 1):
            return None

        if medio_pago == '02':
            cta_debito = mapping.get('cuenta_bancos_tarjeta') or '1.1.1.02.02'
        elif medio_pago in ('04', '03'):
            cta_debito = mapping.get('cuenta_bancos_transferencia') or '1.1.1.02.01'
        else:
            cta_debito = mapping.get('cuenta_caja_efectivo') or '1.1.1.01.01'

        nom_debito = _get_account_name(conn, cid, cta_debito)
        cta_cxc = mapping.get('cuenta_cxc_clientes') or '1.1.2.01'
        nom_cxc = _get_account_name(conn, cid, cta_cxc)

        consecutivo = ar.get('consecutivo', '')
        cliente_nombre = ar.get('cliente_nombre', 'Cliente')

        lineas = [
            {
                'cuenta_codigo': cta_debito,
                'cuenta_nombre': nom_debito,
                'debito': monto,
                'credito': 0.0,
                'referencia_doc': referencia or consecutivo
            },
            {
                'cuenta_codigo': cta_cxc,
                'cuenta_nombre': nom_cxc,
                'debito': 0.0,
                'credito': monto,
                'referencia_doc': referencia or consecutivo
            }
        ]

        asiento_dict = {
            'cliente_id': cid,
            'fecha_contable': date.today().isoformat(),
            'tipo_operacion': 'pago_cxc',
            'documento_origen': f"PAGO-{consecutivo}",
            'modulo_origen': 'cxc',
            'concepto': f"Abono a CxC Factura {consecutivo} - {cliente_nombre} ({referencia})",
            'hash_origen': ar.get('clave_numerica', '')
        }
        asiento_id = database.insert_asiento(conn, asiento_dict, lineas)
        conn.execute(
            "UPDATE accounts_receivable SET asiento_id = ? WHERE id = ? AND company_id = ?",
            (asiento_id, ar_id, cid)
        )
        return asiento_id


def generate_pos_movement_journal_entry(
    company_id: str,
    shift_id: str,
    movement_id: str,
    db_path=None
) -> Optional[str]:
    """
    Genera asiento contable para ingresos y retiros extraordinarios de efectivo en turno POS:
      - Ingreso: Débito Caja POS, Crédito Bancos/Aportes.
      - Retiro: Débito Bancos/Gastos Menores, Crédito Caja POS.
    """
    cid = str(company_id or '').strip()
    if not cid or not movement_id:
        return None

    with database.get_db(db_path) as conn:
        mov_row = conn.execute(
            "SELECT * FROM pos_movements WHERE id = ? AND company_id = ?",
            (movement_id, cid)
        ).fetchone()
        if not mov_row:
            return None
        mov = dict(mov_row)
        if mov.get('asiento_id'):
            return None

        mapping = get_or_create_accounting_mapping(cid, db_path)
        if not mapping.get('auto_generar_asientos', 1):
            return None

        tipo = mov['tipo']
        monto = round(float(mov['monto'] or 0), 2)
        motivo = mov.get('motivo', '')
        if monto <= 0:
            return None

        cta_caja = mapping.get('cuenta_caja_efectivo') or '1.1.1.01.01'
        nom_caja = _get_account_name(conn, cid, cta_caja)
        cta_banco = mapping.get('cuenta_bancos_transferencia') or '1.1.1.02.01'
        nom_banco = _get_account_name(conn, cid, cta_banco)

        if tipo == 'ingreso':
            lineas = [
                {'cuenta_codigo': cta_caja, 'cuenta_nombre': nom_caja, 'debito': monto, 'credito': 0.0, 'referencia_doc': f"SHIFT-{shift_id}"},
                {'cuenta_codigo': cta_banco, 'cuenta_nombre': nom_banco, 'debito': 0.0, 'credito': monto, 'referencia_doc': f"SHIFT-{shift_id}"}
            ]
        else: # retiro
            lineas = [
                {'cuenta_codigo': cta_banco, 'cuenta_nombre': nom_banco, 'debito': monto, 'credito': 0.0, 'referencia_doc': f"SHIFT-{shift_id}"},
                {'cuenta_codigo': cta_caja, 'cuenta_nombre': nom_caja, 'debito': 0.0, 'credito': monto, 'referencia_doc': f"SHIFT-{shift_id}"}
            ]

        asiento_dict = {
            'cliente_id': cid,
            'fecha_contable': date.today().isoformat(),
            'tipo_operacion': f"pos_{tipo}",
            'documento_origen': f"POS-MOV-{movement_id[:8]}",
            'modulo_origen': 'pos',
            'concepto': f"Movimiento POS {tipo.upper()} en Turno #{shift_id}: {motivo}",
            'hash_origen': movement_id
        }
        asiento_id = database.insert_asiento(conn, asiento_dict, lineas)
        conn.execute(
            "UPDATE pos_movements SET asiento_id = ? WHERE id = ? AND company_id = ?",
            (asiento_id, movement_id, cid)
        )
        return asiento_id


def get_document_journal_entries(company_id: str, doc_id: str, db_path=None) -> List[Dict[str, Any]]:
    """
    Retorna los asientos contables vinculados a un comprobante electrónico (venta y costo).
    """
    cid = str(company_id or '').strip()
    if not cid or not doc_id:
        return []

    with database.get_db(db_path) as conn:
        doc_row = conn.execute(
            "SELECT consecutivo, asiento_id, asiento_costo_id FROM electronic_documents WHERE id = ? AND company_id = ?",
            (doc_id, cid)
        ).fetchone()
        if not doc_row:
            return []
        doc = dict(doc_row)

        ids = [i for i in (doc['asiento_id'], doc['asiento_costo_id']) if i]
        if not ids:
            return []

        placeholders = ','.join(['?'] * len(ids))
        asientos_rows = conn.execute(
            f"SELECT * FROM asientos WHERE id IN ({placeholders}) AND cliente_id = ? ORDER BY fecha_contable ASC",
            ids + [cid]
        ).fetchall()

        result = []
        for a in asientos_rows:
            ad = dict(a)
            lines_rows = conn.execute(
                "SELECT * FROM asiento_lineas WHERE asiento_id = ? ORDER BY debito DESC, credito DESC",
                (ad['id'],)
            ).fetchall()
            ad['lineas'] = [dict(l) for l in lines_rows]
            result.append(ad)

        return result
