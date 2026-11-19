"""
FACTURAMI - Servicio de Reportes Comerciales y Fiscales Multiempresa (Fase 8).
Proporciona análisis comercial, tributario (D-104 / IVA), rentabilidad (utilidad bruta),
antigüedad de saldos (CxC) y auditoría de cajas con exportación estructurada a CSV y PDF.
"""
import io
import csv
from datetime import datetime, date
from typing import Dict, Any, List, Optional
import db as database


def _date_clause(field: str, start: Optional[str], end: Optional[str], params: list) -> str:
    clause = ""
    if start:
        clause += f" AND {field} >= ?"
        params.append(start.strip() + " 00:00:00" if len(start.strip()) == 10 else start.strip())
    if end:
        clause += f" AND {field} <= ?"
        params.append(end.strip() + " 23:59:59" if len(end.strip()) == 10 else end.strip())
    return clause


# ══════════════════════════════════════════════════════════════════════════════
# 1. REPORTE FISCAL D-104 / RESUMEN DE IVA MENSUAL
# ══════════════════════════════════════════════════════════════════════════════
def get_d104_iva_report(company_id: str, start_date: Optional[str] = None, end_date: Optional[str] = None, db_path=None) -> Dict[str, Any]:
    """
    Genera el desglose fiscal para la declaración D-104 de Costa Rica.
    Discrimina ventas gravadas por tarifa de IVA (13%, 8%, 4%, 2%, 1%), ventas exentas,
    débito fiscal generado, devoluciones en notas de crédito y neto a declarar.
    """
    cid = str(company_id or '').strip()
    if not cid:
        return {'ok': False, 'error': 'company_id requerido'}

    with database.get_db(db_path) as conn:
        # Obtener documentos de venta aceptados o en proceso en el rango (excluye rechazados)
        params_doc = [cid]
        date_filter = _date_clause("fecha_emision", start_date, end_date, params_doc)

        docs = conn.execute(
            f"""
            SELECT id, tipo_documento, consecutivo, fecha_emision, subtotal as total_venta_neta,
                   total_impuestos, total_comprobante, estado_comprobante
            FROM electronic_documents
            WHERE company_id = ?
              AND estado_comprobante != 'rechazado'
              {date_filter}
            ORDER BY fecha_emision ASC
            """,
            params_doc
        ).fetchall()

        # Desglose de líneas por tarifa de impuesto
        params_lines = [cid]
        date_filter_lines = _date_clause("d.fecha_emision", start_date, end_date, params_lines)

        lines = conn.execute(
            f"""
            SELECT l.subtotal_linea, l.monto_impuesto, l.tarifa_iva_porcentaje, d.tipo_documento
            FROM electronic_document_lines l
            JOIN electronic_documents d ON l.document_id = d.id
            WHERE l.company_id = ?
              AND d.estado_comprobante != 'rechazado'
              {date_filter_lines}
            """,
            params_lines
        ).fetchall()

    # Tarifas estándar en Costa Rica
    tarifas = {13.0: {'base': 0.0, 'impuesto': 0.0},
               8.0: {'base': 0.0, 'impuesto': 0.0},
               4.0: {'base': 0.0, 'impuesto': 0.0},
               2.0: {'base': 0.0, 'impuesto': 0.0},
               1.0: {'base': 0.0, 'impuesto': 0.0},
               0.0: {'base': 0.0, 'impuesto': 0.0}}

    total_ventas_brutas = 0.0
    total_devoluciones_nc = 0.0
    total_iva_debito_bruto = 0.0
    total_iva_nc = 0.0

    for row in lines:
        sub = float(row['subtotal_linea'] or 0.0)
        imp = float(row['monto_impuesto'] or 0.0)
        tarifa = round(float(row['tarifa_iva_porcentaje'] or 0.0), 2)
        tipo = str(row['tipo_documento'] or '01')

        factor = -1.0 if tipo == '03' else 1.0  # NC resta

        if tarifa not in tarifas:
            tarifas[tarifa] = {'base': 0.0, 'impuesto': 0.0}

        tarifas[tarifa]['base'] += sub * factor
        tarifas[tarifa]['impuesto'] += imp * factor

        if tipo == '03':
            total_devoluciones_nc += sub
            total_iva_nc += imp
        else:
            total_ventas_brutas += sub
            total_iva_debito_bruto += imp

    total_ventas_netas = total_ventas_brutas - total_devoluciones_nc
    total_iva_neto = total_iva_debito_bruto - total_iva_nc

    desglose_tarifas = []
    for t in sorted(tarifas.keys(), reverse=True):
        data = tarifas[t]
        if abs(data['base']) > 0.001 or abs(data['impuesto']) > 0.001:
            desglose_tarifas.append({
                'tarifa_porcentaje': t,
                'descripcion': f"Tarifa General ({int(t)}%)" if t == 13 else (f"Tarifa Reducida ({t}%)" if t > 0 else "Exento / Tarifa 0%"),
                'base_imponible': round(data['base'], 2),
                'monto_iva': round(data['impuesto'], 2)
            })

    return {
        'company_id': cid,
        'periodo': {'inicio': start_date or 'inicio', 'fin': end_date or 'actual'},
        'total_documentos': len(docs),
        'resumen': {
            'ventas_brutas': round(total_ventas_brutas, 2),
            'devoluciones_nc': round(total_devoluciones_nc, 2),
            'ventas_netas': round(total_ventas_netas, 2),
            'iva_debito_bruto': round(total_iva_debito_bruto, 2),
            'iva_nc_reversion': round(total_iva_nc, 2),
            'iva_debito_neto_declarar': round(total_iva_neto, 2)
        },
        'desglose_por_tarifa': desglose_tarifas
    }


# ══════════════════════════════════════════════════════════════════════════════
# 2. REPORTE DE VENTAS POR PERÍODO Y FILTROS COMERCIALES
# ══════════════════════════════════════════════════════════════════════════════
def get_ventas_periodo_report(
    company_id: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    customer_id: Optional[str] = None,
    medio_pago: Optional[str] = None,
    condicion_venta: Optional[str] = None,
    tipo_documento: Optional[str] = None,
    db_path=None
) -> Dict[str, Any]:
    """
    Reporte comercial detallado de ventas con filtros avanzados y totales por medio de pago.
    """
    cid = str(company_id or '').strip()
    if not cid:
        return {'ok': False, 'error': 'company_id requerido'}

    params = [cid]
    query = """
        SELECT d.id, d.tipo_documento, d.consecutivo, d.clave_numerica, d.fecha_emision,
               d.customer_nombre, d.customer_identificacion, d.condicion_venta, d.medio_pago,
               d.subtotal as total_venta_neta, d.total_impuestos, d.total_comprobante, d.estado_comprobante,
               d.moneda
        FROM electronic_documents d
        WHERE d.company_id = ?
          AND d.estado_comprobante != 'rechazado'
    """

    query += _date_clause("d.fecha_emision", start_date, end_date, params)

    if customer_id:
        query += " AND d.customer_id = ?"
        params.append(customer_id)
    if medio_pago:
        query += " AND d.medio_pago = ?"
        params.append(medio_pago)
    if condicion_venta:
        query += " AND d.condicion_venta = ?"
        params.append(condicion_venta)
    if tipo_documento:
        query += " AND d.tipo_documento = ?"
        params.append(tipo_documento)

    query += " ORDER BY d.fecha_emision DESC"

    with database.get_db(db_path) as conn:
        rows = conn.execute(query, params).fetchall()
        items = [dict(r) for r in rows]

    total_subtotal = 0.0
    total_iva = 0.0
    total_general = 0.0
    por_medio_pago = {'efectivo': 0.0, 'tarjeta': 0.0, 'transferencia': 0.0, 'credito': 0.0, 'otros': 0.0}

    for item in items:
        tipo = item.get('tipo_documento')
        factor = -1.0 if tipo == '03' else 1.0

        sub = float(item.get('total_venta_neta') or 0.0) * factor
        iva = float(item.get('total_impuestos') or 0.0) * factor
        tot = float(item.get('total_comprobante') or 0.0) * factor

        total_subtotal += sub
        total_iva += iva
        total_general += tot

        mp = item.get('medio_pago', '01')
        cond = item.get('condicion_venta', '01')

        if cond == '02':
            por_medio_pago['credito'] += tot
        elif mp == '01':
            por_medio_pago['efectivo'] += tot
        elif mp == '02':
            por_medio_pago['tarjeta'] += tot
        elif mp in ('04', 'sinpe', 'transferencia'):
            por_medio_pago['transferencia'] += tot
        else:
            por_medio_pago['otros'] += tot

    return {
        'company_id': cid,
        'total_registros': len(items),
        'totales': {
            'subtotal': round(total_subtotal, 2),
            'iva': round(total_iva, 2),
            'total': round(total_general, 2)
        },
        'por_medio_pago': {k: round(v, 2) for k, v in por_medio_pago.items()},
        'documentos': items
    }


# ══════════════════════════════════════════════════════════════════════════════
# 3. REPORTE DE UTILIDAD BRUTA Y MARGEN POR PRODUCTO
# ══════════════════════════════════════════════════════════════════════════════
def get_utilidad_bruta_report(
    company_id: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    db_path=None
) -> Dict[str, Any]:
    """
    Calcula ventas netas, costo de ventas y margen de utilidad bruta por producto y total.
    Utilidad = Venta Neta - Costo de Ventas.
    Margen % = (Utilidad / Venta Neta) * 100.
    """
    cid = str(company_id or '').strip()
    if not cid:
        return {'ok': False, 'error': 'company_id requerido'}

    params = [cid]
    date_filter = _date_clause("d.fecha_emision", start_date, end_date, params)

    query = f"""
        SELECT l.product_id,
               COALESCE(NULLIF(l.codigo_producto, ''), p.codigo_interno, 'ITEM') as codigo,
               COALESCE(NULLIF(l.descripcion, ''), p.nombre, 'Sin descripción') as descripcion,
               SUM(CASE WHEN d.tipo_documento = '03' THEN -l.cantidad ELSE l.cantidad END) as cantidad_total,
               SUM(CASE WHEN d.tipo_documento = '03' THEN -l.subtotal_linea ELSE l.subtotal_linea END) as venta_neta_total,
               COALESCE(p.precio_compra, 0.0) as costo_unitario_actual
        FROM electronic_document_lines l
        JOIN electronic_documents d ON l.document_id = d.id
        LEFT JOIN products p ON l.product_id = p.id
        WHERE l.company_id = ?
          AND d.estado_comprobante != 'rechazado'
          {date_filter}
        GROUP BY l.product_id, l.codigo_producto, l.descripcion
        ORDER BY venta_neta_total DESC
    """

    with database.get_db(db_path) as conn:
        rows = conn.execute(query, params).fetchall()

    items = []
    gran_total_venta = 0.0
    gran_total_costo = 0.0
    gran_total_utilidad = 0.0

    for r in rows:
        cant = float(r['cantidad_total'] or 0.0)
        venta = float(r['venta_neta_total'] or 0.0)
        costo_unit = float(r['costo_unitario_actual'] or 0.0)
        costo_total = cant * costo_unit
        utilidad = venta - costo_total
        margen_pct = (utilidad / venta * 100.0) if abs(venta) > 0.001 else 0.0

        gran_total_venta += venta
        gran_total_costo += costo_total
        gran_total_utilidad += utilidad

        items.append({
            'product_id': r['product_id'],
            'codigo': r['codigo'] or '—',
            'descripcion': r['descripcion'] or 'Sin descripción',
            'cantidad_vendida': round(cant, 2),
            'costo_unitario': round(costo_unit, 2),
            'venta_neta': round(venta, 2),
            'costo_total': round(costo_total, 2),
            'utilidad_bruta': round(utilidad, 2),
            'margen_porcentaje': round(margen_pct, 2)
        })

    margen_global = (gran_total_utilidad / gran_total_venta * 100.0) if abs(gran_total_venta) > 0.001 else 0.0

    return {
        'company_id': cid,
        'resumen_general': {
            'venta_neta_total': round(gran_total_venta, 2),
            'costo_ventas_total': round(gran_total_costo, 2),
            'utilidad_bruta_total': round(gran_total_utilidad, 2),
            'margen_bruto_promedio_pct': round(margen_global, 2)
        },
        'productos': items
    }


# ══════════════════════════════════════════════════════════════════════════════
# 4. REPORTE DE ANTIGÜEDAD DE SALDOS (CUENTAS POR COBRAR)
# ══════════════════════════════════════════════════════════════════════════════
def get_antiguedad_saldos_report(company_id: str, db_path=None) -> Dict[str, Any]:
    """
    Clasifica las cuentas por cobrar pendientes en cubos de antigüedad:
    - Al día / No vencido
    - 1 a 30 días de vencido
    - 31 a 60 días de vencido
    - 61 a 90 días de vencido
    - Más de 90 días de vencido (+90)
    """
    cid = str(company_id or '').strip()
    if not cid:
        return {'ok': False, 'error': 'company_id requerido'}

    with database.get_db(db_path) as conn:
        rows = conn.execute(
            """
            SELECT ar.id, ar.document_id, ar.customer_id, ar.customer_nombre,
                   COALESCE(ed.consecutivo, ar.document_id) as consecutivo,
                   ar.monto_original, ar.monto_abonado, ar.saldo_pendiente, ar.fecha_emision,
                   ar.fecha_vencimiento, ar.estado
            FROM accounts_receivable ar
            LEFT JOIN electronic_documents ed ON ar.document_id = ed.id
            WHERE ar.company_id = ?
              AND ar.saldo_pendiente > 0.001
            ORDER BY ar.fecha_vencimiento ASC
            """,
            (cid,)
        ).fetchall()
        items = [dict(r) for r in rows]

    today = date.today()

    cubos = {
        'al_dia': {'monto': 0.0, 'cantidad': 0},
        'dias_1_30': {'monto': 0.0, 'cantidad': 0},
        'dias_31_60': {'monto': 0.0, 'cantidad': 0},
        'dias_61_90': {'monto': 0.0, 'cantidad': 0},
        'mas_90': {'monto': 0.0, 'cantidad': 0}
    }

    total_cartera = 0.0

    for ar in items:
        saldo = float(ar.get('saldo_pendiente') or 0.0)
        total_cartera += saldo

        venc_str = str(ar.get('fecha_vencimiento') or '')[:10]
        try:
            venc_date = datetime.strptime(venc_str, '%Y-%m-%d').date()
            dias_mora = (today - venc_date).days
        except Exception:
            dias_mora = 0

        ar['dias_mora'] = dias_mora

        if dias_mora <= 0:
            cubo = 'al_dia'
            ar['clasificacion'] = 'Al Día'
        elif dias_mora <= 30:
            cubo = 'dias_1_30'
            ar['clasificacion'] = '1-30 días vencido'
        elif dias_mora <= 60:
            cubo = 'dias_31_60'
            ar['clasificacion'] = '31-60 días vencido'
        elif dias_mora <= 90:
            cubo = 'dias_61_90'
            ar['clasificacion'] = '61-90 días vencido'
        else:
            cubo = 'mas_90'
            ar['clasificacion'] = '+90 días vencido'

        cubos[cubo]['monto'] += saldo
        cubos[cubo]['cantidad'] += 1

    return {
        'company_id': cid,
        'fecha_corte': str(today),
        'total_cartera_pendiente': round(total_cartera, 2),
        'total_documentos_pendientes': len(items),
        'cubos_antiguedad': {k: {'monto': round(v['monto'], 2), 'cantidad': v['cantidad']} for k, v in cubos.items()},
        'detalle_cuentas': items
    }


# ══════════════════════════════════════════════════════════════════════════════
# 5. REPORTE DE CIERRES Y ARQUEOS DE CAJA POS
# ══════════════════════════════════════════════════════════════════════════════
def get_cierres_caja_report(
    company_id: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    db_path=None
) -> Dict[str, Any]:
    """
    Auditoría de aperturas y cierres de turno de caja, comparando
    el efectivo esperado con el conteo real y diferencias (faltantes/sobrantes).
    """
    cid = str(company_id or '').strip()
    if not cid:
        return {'ok': False, 'error': 'company_id requerido'}

    params = [cid]
    query = """
        SELECT id, codigo_sucursal, codigo_terminal, cajero_username as usuario,
               monto_apertura, efectivo_esperado as monto_cierre_esperado,
               efectivo_real as monto_cierre_real, diferencia,
               total_ventas_efectivo, total_ventas_tarjeta, total_ventas_otros,
               total_ingresos_extra as total_ingresos_efectivo,
               total_retiros_caja as total_retiros_efectivo,
               fecha_apertura, fecha_cierre, estado, observaciones_cierre as observaciones
        FROM pos_shifts
        WHERE company_id = ?
    """
    query += _date_clause("fecha_apertura", start_date, end_date, params)
    query += " ORDER BY fecha_apertura DESC"

    with database.get_db(db_path) as conn:
        rows = conn.execute(query, params).fetchall()
        items = [dict(r) for r in rows]

    total_ventas_efectivo = sum(float(r.get('total_ventas_efectivo') or 0.0) for r in items)
    total_ventas_tarjeta = sum(float(r.get('total_ventas_tarjeta') or 0.0) for r in items)
    total_diferencia_neta = sum(float(r.get('diferencia') or 0.0) for r in items if r.get('estado') in ('cerrado', 'cerrada'))

    return {
        'company_id': cid,
        'total_turnos': len(items),
        'resumen': {
            'total_ventas_efectivo': round(total_ventas_efectivo, 2),
            'total_ventas_tarjeta': round(total_ventas_tarjeta, 2),
            'diferencia_neta_arqueo': round(total_diferencia_neta, 2)
        },
        'turnos': items
    }


# ══════════════════════════════════════════════════════════════════════════════
# 6. EXPORTADORES A CSV Y PDF
# ══════════════════════════════════════════════════════════════════════════════
def export_report_to_csv(report_type: str, data: Dict[str, Any]) -> str:
    """
    Convierte los datos estructurados de cualquier reporte a formato CSV estándar RFC 4180.
    """
    output = io.StringIO()
    writer = csv.writer(output, delimiter=',', quoting=csv.QUOTE_MINIMAL)

    if report_type == 'd104':
        writer.writerow(['REPORTE FISCAL D-104 / RESUMEN DE IVA'])
        writer.writerow(['Empresa', data.get('company_id', '')])
        writer.writerow(['Periodo', f"{data.get('periodo', {}).get('inicio')} al {data.get('periodo', {}).get('fin')}"])
        writer.writerow([])
        writer.writerow(['Concepto General', 'Monto'])
        res = data.get('resumen', {})
        writer.writerow(['Ventas Brutas', res.get('ventas_brutas', 0.0)])
        writer.writerow(['Devoluciones y Notas de Credito (-)', res.get('devoluciones_nc', 0.0)])
        writer.writerow(['Ventas Netas', res.get('ventas_netas', 0.0)])
        writer.writerow(['IVA Debito Bruto', res.get('iva_debito_bruto', 0.0)])
        writer.writerow(['IVA Notas de Credito (-)', res.get('iva_nc_reversion', 0.0)])
        writer.writerow(['IVA Debito Neto a Declarar', res.get('iva_debito_neto_declarar', 0.0)])
        writer.writerow([])
        writer.writerow(['DESGLOSE POR TARIFA'])
        writer.writerow(['Tarifa (%)', 'Descripcion', 'Base Imponible', 'Monto IVA'])
        for d in data.get('desglose_por_tarifa', []):
            writer.writerow([d['tarifa_porcentaje'], d['descripcion'], d['base_imponible'], d['monto_iva']])

    elif report_type == 'sales':
        writer.writerow(['REPORTE DE VENTAS POR PERIODO'])
        writer.writerow(['Empresa', data.get('company_id', '')])
        writer.writerow(['Total Documentos', data.get('total_registros', 0)])
        tot = data.get('totales', {})
        writer.writerow(['Subtotal Neto', tot.get('subtotal', 0.0)])
        writer.writerow(['Total IVA', tot.get('iva', 0.0)])
        writer.writerow(['Total General', tot.get('total', 0.0)])
        writer.writerow([])
        writer.writerow(['Fecha', 'Tipo', 'Consecutivo', 'Cliente', 'Cedula', 'Condicion', 'Medio Pago', 'Subtotal', 'IVA', 'Total', 'Estado'])
        for doc in data.get('documentos', []):
            writer.writerow([
                doc.get('fecha_emision', ''),
                doc.get('tipo_documento', ''),
                doc.get('consecutivo', ''),
                doc.get('customer_nombre', ''),
                doc.get('customer_identificacion', ''),
                doc.get('condicion_venta', ''),
                doc.get('medio_pago', ''),
                doc.get('total_venta_neta', 0.0),
                doc.get('total_impuestos', 0.0),
                doc.get('total_comprobante', 0.0),
                doc.get('estado_comprobante', '')
            ])

    elif report_type == 'profitability':
        writer.writerow(['REPORTE DE UTILIDAD BRUTA Y MARGEN POR PRODUCTO'])
        writer.writerow(['Empresa', data.get('company_id', '')])
        rg = data.get('resumen_general', {})
        writer.writerow(['Venta Neta Total', rg.get('venta_neta_total', 0.0)])
        writer.writerow(['Costo de Ventas Total', rg.get('costo_ventas_total', 0.0)])
        writer.writerow(['Utilidad Bruta Total', rg.get('utilidad_bruta_total', 0.0)])
        writer.writerow(['Margen Promedio (%)', rg.get('margen_bruto_promedio_pct', 0.0)])
        writer.writerow([])
        writer.writerow(['Codigo', 'Descripcion', 'Cant Vendida', 'Costo Unit', 'Venta Neta', 'Costo Total', 'Utilidad Bruta', 'Margen (%)'])
        for p in data.get('productos', []):
            writer.writerow([
                p.get('codigo', ''),
                p.get('descripcion', ''),
                p.get('cantidad_vendida', 0.0),
                p.get('costo_unitario', 0.0),
                p.get('venta_neta', 0.0),
                p.get('costo_total', 0.0),
                p.get('utilidad_bruta', 0.0),
                p.get('margen_porcentaje', 0.0)
            ])

    elif report_type == 'cxc_aging':
        writer.writerow(['REPORTE DE ANTIGUEDAD DE SALDOS CXC'])
        writer.writerow(['Empresa', data.get('company_id', '')])
        writer.writerow(['Fecha Corte', data.get('fecha_corte', '')])
        writer.writerow(['Total Cartera Pendiente', data.get('total_cartera_pendiente', 0.0)])
        writer.writerow([])
        writer.writerow(['CUBOS DE ANTIGUEDAD'])
        for k, v in data.get('cubos_antiguedad', {}).items():
            writer.writerow([k, v.get('monto', 0.0), f"{v.get('cantidad', 0)} docs"])
        writer.writerow([])
        writer.writerow(['Consecutivo', 'Cliente', 'Cedula', 'Emision', 'Vencimiento', 'Mora (dias)', 'Original', 'Abonado', 'Saldo Pendiente', 'Clasificacion'])
        for cxc in data.get('detalle_cuentas', []):
            writer.writerow([
                cxc.get('consecutivo', ''),
                cxc.get('customer_nombre', ''),
                cxc.get('customer_identificacion', ''),
                cxc.get('fecha_emision', ''),
                cxc.get('fecha_vencimiento', ''),
                cxc.get('dias_mora', 0),
                cxc.get('monto_original', 0.0),
                cxc.get('monto_abonado', 0.0),
                cxc.get('saldo_pendiente', 0.0),
                cxc.get('clasificacion', '')
            ])

    elif report_type == 'pos_shifts':
        writer.writerow(['REPORTE DE AUDITORIA DE CIERRES DE CAJA POS'])
        writer.writerow(['Empresa', data.get('company_id', '')])
        writer.writerow(['Total Turnos', data.get('total_turnos', 0)])
        res = data.get('resumen', {})
        writer.writerow(['Ventas Efectivo', res.get('total_ventas_efectivo', 0.0)])
        writer.writerow(['Ventas Tarjeta', res.get('total_ventas_tarjeta', 0.0)])
        writer.writerow(['Diferencia Neta Arqueo', res.get('diferencia_neta_arqueo', 0.0)])
        writer.writerow([])
        writer.writerow(['Turno ID', 'Usuario', 'Apertura', 'Cierre', 'Fondo Inicial', 'Esperado', 'Real', 'Diferencia', 'Estado'])
        for s in data.get('turnos', []):
            writer.writerow([
                s.get('id', ''),
                s.get('usuario', ''),
                s.get('fecha_apertura', ''),
                s.get('fecha_cierre', ''),
                s.get('monto_apertura', 0.0),
                s.get('monto_cierre_esperado', 0.0),
                s.get('monto_cierre_real', 0.0),
                s.get('diferencia', 0.0),
                s.get('estado', '')
            ])

    return output.getvalue()


def export_report_to_pdf_bytes(report_type: str, data: Dict[str, Any]) -> bytes:
    """
    Genera un reporte PDF estructurado utilizando reportlab (o genera un PDF mínimo estándar).
    """
    try:
        from reportlab.lib.pagesizes import letter
        from reportlab.pdfgen import canvas

        buffer = io.BytesIO()
        p = canvas.Canvas(buffer, pagesize=letter)
        width, height = letter

        # Título y Encabezado
        p.setFont("Helvetica-Bold", 14)
        p.drawString(40, height - 50, f"FACTURAMI · {report_type.upper().replace('_', ' ')}")
        p.setFont("Helvetica", 9)
        p.drawString(40, height - 66, f"Empresa: {data.get('company_id')} | Fecha de Emisión: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        p.line(40, height - 72, width - 40, height - 72)

        y = height - 95
        p.setFont("Helvetica-Bold", 10)

        if report_type == 'd104':
            p.drawString(40, y, "RESUMEN TRIBUTARIO D-104 (IVA MENSUAL)")
            y -= 18
            p.setFont("Helvetica", 9)
            res = data.get('resumen', {})
            for k, lbl in [
                ('ventas_brutas', 'Ventas Brutas Gravadas y Exentas:'),
                ('devoluciones_nc', 'Devoluciones en Notas de Crédito (-):'),
                ('ventas_netas', 'Ventas Netas Totales:'),
                ('iva_debito_bruto', 'IVA Débito Fiscal Bruto:'),
                ('iva_nc_reversion', 'IVA Acreditado en Notas de Crédito (-):'),
                ('iva_debito_neto_declarar', 'TOTAL IVA DÉBITO FISCAL A DECLARAR:')
            ]:
                p.drawString(50, y, lbl)
                p.drawRightString(width - 50, y, f"CRC {res.get(k, 0.0):,.2f}")
                y -= 16

            y -= 10
            p.setFont("Helvetica-Bold", 10)
            p.drawString(40, y, "DESGLOSE POR TARIFA DE IVA")
            y -= 16
            p.setFont("Helvetica", 9)
            for d in data.get('desglose_por_tarifa', []):
                p.drawString(50, y, f"{d['descripcion']}: Base CRC {d['base_imponible']:,.2f}")
                p.drawRightString(width - 50, y, f"IVA CRC {d['monto_iva']:,.2f}")
                y -= 14

        elif report_type == 'sales':
            tot = data.get('totales', {})
            p.drawString(40, y, f"VENTAS TOTALES: CRC {tot.get('total', 0.0):,.2f}  (Subtotal: CRC {tot.get('subtotal', 0.0):,.2f} | IVA: CRC {tot.get('iva', 0.0):,.2f})")
            y -= 20
            p.setFont("Helvetica-Bold", 8)
            p.drawString(40, y, "Fecha")
            p.drawString(100, y, "Consecutivo")
            p.drawString(200, y, "Cliente")
            p.drawString(360, y, "Medio")
            p.drawRightString(width - 40, y, "Total (CRC)")
            y -= 12
            p.setFont("Helvetica", 8)
            for doc in data.get('documentos', [])[:35]:
                p.drawString(40, y, str(doc.get('fecha_emision', ''))[:10])
                p.drawString(100, y, str(doc.get('consecutivo', ''))[-12:])
                p.drawString(200, y, str(doc.get('customer_nombre', ''))[:28])
                p.drawString(360, y, str(doc.get('medio_pago', '')))
                p.drawRightString(width - 40, y, f"{float(doc.get('total_comprobante') or 0.0):,.2f}")
                y -= 12
                if y < 50:
                    break

        elif report_type == 'profitability':
            rg = data.get('resumen_general', {})
            p.drawString(40, y, f"UTILIDAD BRUTA TOTAL: CRC {rg.get('utilidad_bruta_total', 0.0):,.2f} (Margen: {rg.get('margen_bruto_promedio_pct', 0.0):.1f}%)")
            y -= 20
            p.setFont("Helvetica-Bold", 8)
            p.drawString(40, y, "Código")
            p.drawString(100, y, "Descripción")
            p.drawString(260, y, "Cant.")
            p.drawRightString(360, y, "Venta Neta")
            p.drawRightString(460, y, "Utilidad")
            p.drawRightString(width - 40, y, "Margen %")
            y -= 12
            p.setFont("Helvetica", 8)
            for prod in data.get('productos', [])[:35]:
                p.drawString(40, y, str(prod.get('codigo', ''))[:10])
                p.drawString(100, y, str(prod.get('descripcion', ''))[:26])
                p.drawString(260, y, str(prod.get('cantidad_vendida', 0)))
                p.drawRightString(360, y, f"{prod.get('venta_neta', 0.0):,.2f}")
                p.drawRightString(460, y, f"{prod.get('utilidad_bruta', 0.0):,.2f}")
                p.drawRightString(width - 40, y, f"{prod.get('margen_porcentaje', 0.0):.1f}%")
                y -= 12
                if y < 50:
                    break

        elif report_type == 'cxc_aging':
            p.drawString(40, y, f"CARTERA TOTAL PENDIENTE: CRC {data.get('total_cartera_pendiente', 0.0):,.2f}")
            y -= 16
            p.setFont("Helvetica", 8)
            for k, v in data.get('cubos_antiguedad', {}).items():
                p.drawString(40, y, f"{k}: CRC {v['monto']:,.2f} ({v['cantidad']} facturas)")
                y -= 12
            y -= 8
            p.setFont("Helvetica-Bold", 8)
            p.drawString(40, y, "Consecutivo")
            p.drawString(140, y, "Cliente")
            p.drawString(320, y, "Vencimiento")
            p.drawRightString(440, y, "Mora (días)")
            p.drawRightString(width - 40, y, "Saldo Pendiente")
            y -= 12
            p.setFont("Helvetica", 8)
            for cxc in data.get('detalle_cuentas', [])[:35]:
                p.drawString(40, y, str(cxc.get('consecutivo', ''))[-12:])
                p.drawString(140, y, str(cxc.get('customer_nombre', ''))[:28])
                p.drawString(320, y, str(cxc.get('fecha_vencimiento', ''))[:10])
                p.drawRightString(440, y, str(cxc.get('dias_mora', 0)))
                p.drawRightString(width - 40, y, f"{float(cxc.get('saldo_pendiente') or 0.0):,.2f}")
                y -= 12
                if y < 50:
                    break

        elif report_type == 'pos_shifts':
            res = data.get('resumen', {})
            p.drawString(40, y, f"AUDITORÍA CAJAS: Efectivo CRC {res.get('total_ventas_efectivo', 0.0):,.2f} | Diferencia Arqueo: CRC {res.get('diferencia_neta_arqueo', 0.0):,.2f}")
            y -= 20
            p.setFont("Helvetica-Bold", 8)
            p.drawString(40, y, "Usuario")
            p.drawString(120, y, "Apertura")
            p.drawString(220, y, "Cierre")
            p.drawRightString(340, y, "Esperado")
            p.drawRightString(440, y, "Real")
            p.drawRightString(width - 40, y, "Diferencia")
            y -= 12
            p.setFont("Helvetica", 8)
            for s in data.get('turnos', [])[:35]:
                p.drawString(40, y, str(s.get('usuario', ''))[:12])
                p.drawString(120, y, str(s.get('fecha_apertura', ''))[:16])
                p.drawString(220, y, str(s.get('fecha_cierre', ''))[:16])
                p.drawRightString(340, y, f"{float(s.get('monto_cierre_esperado') or 0.0):,.2f}")
                p.drawRightString(440, y, f"{float(s.get('monto_cierre_real') or 0.0):,.2f}")
                p.drawRightString(width - 40, y, f"{float(s.get('diferencia') or 0.0):,.2f}")
                y -= 12
                if y < 50:
                    break

        p.showPage()
        p.save()
        buffer.seek(0)
        return buffer.getvalue()
    except ImportError:
        # Fallback a PDF binario mínimo si reportlab no estuviese disponible
        content = f"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n3 0 obj<</Type/Page/MediaBox[0 0 612 792]/Parent 2 0 R>>endobj\nxref\n0 4\n0000000000 65535 f \n0000000009 00000 n \n0000000052 00000 n \n0000000108 00000 n \ntrailer<</Size 4/Root 1 0 R>>\nstartxref\n180\n%%EOF"
        return content.encode('latin1')
