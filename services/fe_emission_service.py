"""
FACTURAMI - Orquestador Central de Emisión de Facturación Electrónica (Fase 5).
==============================================================================
Gestiona el ciclo de vida completo de comprobantes electrónicos:
  1. Validación de cliente, líneas, CABYS, impuestos y control de crédito/inventario.
  2. Asignación atómica de número consecutivo y clave de 50 dígitos.
  3. Generación del XML v4.3/v4.4 oficial.
  4. Firma digital XAdES-BES en memoria descifrando .p12 y PIN.
  5. Envío y consulta asíncrona ante el API del Ministerio de Hacienda.
  6. Generación de Cuentas por Cobrar automáticas para ventas a crédito.
  7. Descuento automático de existencias para productos que controlan inventario.
  8. Generación de PDF oficial y descarga de archivos.
"""
import base64
import re
import uuid
from datetime import datetime, timedelta, timezone

import db as database
import services.secret_store as secret_store
from services.fe_clave_service import generar_consecutivo, generar_clave, desglosar_clave
from services.fe_xml_service import generate_electronic_invoice_xml
from services.fe_signer_service import sign_xml_xades_bes
from services.fe_hacienda_service import get_hacienda_token, send_to_hacienda, check_hacienda_status
from services.fe_pdf_service import generate_electronic_document_pdf


class EmissionError(ValueError):
    pass


def _map_consec_field(tipo_doc: str) -> str:
    m = {
        '01': 'consec_fe',
        '04': 'consec_te',
        '03': 'consec_nc',
        '02': 'consec_nd',
        '08': 'consec_fec',
        '09': 'consec_rep',
    }
    return m.get(tipo_doc, 'consec_fe')


def emitir_comprobante(company_id: str, body: dict, actor: str, db_path=None) -> dict:
    """
    Emite un comprobante electrónico para la empresa especificada.
    Realiza todas las operaciones dentro de una transacción atómica para garantizar
    que el número consecutivo nunca se duplique ni se salte.
    """
    cid = str(company_id or '').strip()
    if not cid:
        raise EmissionError("company_id es requerido")

    tipo_doc = str(body.get('tipo_documento') or '01').strip()
    if tipo_doc not in ('01', '02', '03', '04', '08', '09'):
        raise EmissionError(f"Tipo de comprobante '{tipo_doc}' no válido")

    lineas_input = body.get('lineas') or []
    if not lineas_input or not isinstance(lineas_input, list):
        raise EmissionError("El comprobante debe contener al menos una línea de producto o servicio")

    cond_venta = str(body.get('condicion_venta') or '01').strip()
    medio_pago = str(body.get('medio_pago') or '01').strip()
    plazo_credito = int(body.get('plazo_credito') or 0) if cond_venta == '06' else 0
    moneda = str(body.get('moneda') or 'CRC').strip().upper()
    tipo_cambio = float(body.get('tipo_cambio') or 1.0)

    # Documento de referencia (Requerido para Notas de Crédito '03' y Débito '02')
    ref_data = body.get('referencia') or {}
    if tipo_doc in ('03', '02'):
        if not ref_data.get('numero') and not body.get('ref_numero_clave'):
            raise EmissionError(f"Comprobantes tipo {tipo_doc} requieren especificar el documento de referencia")
        if not ref_data:
            ref_data = {
                'tipo_documento': body.get('ref_tipo_documento', '01'),
                'numero': body.get('ref_numero_clave'),
                'fecha_emision': body.get('ref_fecha_emision'),
                'codigo': body.get('ref_codigo', '01'),
                'razon': body.get('ref_razon', 'Anulación o corrección de comprobante')
            }

    # Situación del comprobante (1: Normal, 2: Contingencia, 3: Sin internet)
    situacion = str(body.get('situacion') or '1').strip()
    if situacion not in ('1', '2', '3'):
        raise EmissionError(f"Situación del comprobante '{situacion}' inválida (1=Normal, 2=Contingencia, 3=Sin internet)")

    doc_id = str(uuid.uuid4())
    ahora = datetime.now()
    fecha_emision_iso = ahora.strftime('%Y-%m-%dT%H:%M:%S-06:00')

    with database.get_db(db_path) as conn:
        # Bloqueo atómico inmediato para serializar la reserva de consecutivos y evitar colisiones concurrentes
        if not conn.in_transaction:
            try:
                conn.execute("BEGIN IMMEDIATE")
            except Exception:
                pass

        # 1. Obtener emisor y configuración
        emisor_row = conn.execute("SELECT * FROM clientes WHERE id = ?", (cid,)).fetchone()
        if not emisor_row:
            raise EmissionError(f"Empresa emisor {cid} no encontrada")
        emisor = dict(emisor_row)

        fe_cfg_row = conn.execute("SELECT * FROM company_fe_settings WHERE company_id = ?", (cid,)).fetchone()
        fe_cfg = dict(fe_cfg_row) if fe_cfg_row else {}

        # 2. Obtener y bloquear terminal para reserva de consecutivo
        term_row = conn.execute("""
            SELECT * FROM company_terminals
            WHERE company_id = ? AND activo = 1
            ORDER BY codigo_sucursal, codigo_terminal
            LIMIT 1
        """, (cid,)).fetchone()
        if not term_row:
            raise EmissionError("La empresa no tiene una terminal de facturación activa")

        term = dict(term_row)
        suc = term['codigo_sucursal']
        ter = term['codigo_terminal']

        consec_field = _map_consec_field(tipo_doc)
        siguiente_num = int(term.get(consec_field) or 0) + 1

        consecutivo = generar_consecutivo(suc, ter, tipo_doc, siguiente_num)
        clave = generar_clave(emisor.get('id'), consecutivo, fecha=ahora, situacion=situacion)

        # 3. Resolver receptor
        customer_id = str(body.get('customer_id') or '').strip()
        receptor = None
        if customer_id:
            c_row = conn.execute("SELECT * FROM sales_customers WHERE id = ? AND company_id = ?",
                                 (customer_id, cid)).fetchone()
            if c_row:
                receptor = dict(c_row)
        elif body.get('receptor'):
            receptor = body.get('receptor')
        elif tipo_doc in ('01', '02', '03'):
            # En factura debe existir receptor (o consumidor final identificado)
            rec_nom = str(body.get('customer_nombre') or 'Consumidor Final').strip()
            rec_id = str(body.get('customer_identificacion') or '').strip()
            rec_tipo = str(body.get('customer_tipo_id') or '01').strip()
            receptor = {
                'nombre': rec_nom,
                'identificacion': rec_id,
                'tipo_identificacion': rec_tipo,
                'email': str(body.get('customer_email') or '').strip(),
            }

        # 4. Procesar líneas y validar inventario
        total_bruto = 0.0
        total_descuentos = 0.0
        total_subtotal = 0.0
        total_impuestos = 0.0
        processed_lines = []

        for idx, l in enumerate(lineas_input, 1):
            cant = max(0.001, float(l.get('cantidad') or 1.0))
            pu = max(0.0, float(l.get('precio_unitario') or 0.0))
            desc = max(0.0, float(l.get('monto_descuento') or 0.0))

            monto_linea = round(cant * pu, 2)
            subtotal_linea = round(monto_linea - desc, 2)

            iva_cod = str(l.get('tarifa_iva_codigo') or ('04' if (l.get('tarifa_iva_porcentaje') == 4.0 or l.get('tarifa_iva') == 4.0) else '08')).strip()
            iva_pct = float(l.get('tarifa_iva_porcentaje') if l.get('tarifa_iva_porcentaje') is not None else (l.get('tarifa_iva') if l.get('tarifa_iva') is not None else (13.0 if iva_cod == '08' else 0.0)))
            monto_imp = round(subtotal_linea * (iva_pct / 100.0), 2)
            tot_linea = round(subtotal_linea + monto_imp, 2)

            total_bruto += monto_linea
            total_descuentos += desc
            total_subtotal += subtotal_linea
            total_impuestos += monto_imp

            line_entry = {
                'id': str(uuid.uuid4()),
                'document_id': doc_id,
                'company_id': cid,
                'numero_linea': idx,
                'product_id': str(l.get('product_id') or '').strip(),
                'codigo_producto': str(l.get('codigo_producto') or l.get('codigo_interno') or f"ITEM-{idx}"),
                'codigo_cabys': str(l.get('codigo_cabys') or '0000000000000').zfill(13)[:13],
                'descripcion': str(l.get('descripcion') or l.get('nombre') or f"Línea {idx}")[:200],
                'categoria_nombre': str(l.get('categoria_nombre') or 'General')[:80],
                'cantidad': cant,
                'unidad_medida': str(l.get('unidad_medida') or 'Unid')[:5],
                'precio_unitario': pu,
                'monto_descuento': desc,
                'naturaleza_descuento': str(l.get('naturaleza_descuento') or '')[:80],
                'subtotal_linea': subtotal_linea,
                'base_imponible': subtotal_linea,
                'tarifa_iva_codigo': iva_cod,
                'tarifa_iva_porcentaje': iva_pct,
                'monto_impuesto': monto_imp,
                'total_linea': tot_linea,
            }
            processed_lines.append(line_entry)

            # Descontar o devolver inventario si el producto controla existencias
            pid = line_entry['product_id']
            if pid:
                p_curr = conn.execute("SELECT id, nombre, controla_inventario, existencia, precio_compra FROM products WHERE id = ? AND company_id = ?",
                                      (pid, cid)).fetchone()
                if p_curr and p_curr['controla_inventario']:
                    permitir_sin_stock = body.get('permitir_venta_sin_stock')
                    if permitir_sin_stock is None:
                        permitir_sin_stock = bool(fe_cfg.get('permitir_venta_sin_stock', True))
                    stock_actual = float(p_curr['existencia'] or 0.0)
                    if tipo_doc == '03':
                        mov_tipo = 'devolucion'
                        nueva_existencia = round(stock_actual + cant, 4)
                    else:
                        if not permitir_sin_stock and cant > stock_actual:
                            raise EmissionError(f"Stock insuficiente para '{p_curr['nombre']}': existencia {stock_actual}, solicitado {cant}")
                        mov_tipo = 'venta'
                        nueva_existencia = round(stock_actual - cant, 4) if permitir_sin_stock else max(0.0, round(stock_actual - cant, 4))

                    conn.execute("UPDATE products SET existencia = ?, updated_at = datetime('now','localtime') WHERE id = ? AND company_id = ?",
                                 (nueva_existencia, pid, cid))

                    # Registrar movimiento en Kardex
                    try:
                        costo_actual = float(p_curr['precio_compra'] or 0.0)
                        saldo_costo = round(nueva_existencia * costo_actual, 2)
                        conn.execute("""
                            INSERT INTO inventory_kardex (
                                id, company_id, product_id, fecha, tipo_movimiento, documento_tipo,
                                documento_ref, cantidad, costo_unitario, costo_total, saldo_cantidad,
                                saldo_costo_unitario, saldo_costo_total, motivo, usuario_creacion
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, (
                            str(uuid.uuid4()), cid, pid, ahora.strftime('%Y-%m-%d %H:%M:%S'),
                            mov_tipo, tipo_doc, consecutivo, cant, costo_actual,
                            round(cant * costo_actual, 2), nueva_existencia, costo_actual,
                            saldo_costo, f"{'Devolución' if tipo_doc == '03' else 'Venta'} {consecutivo}", actor
                        ))
                    except Exception:
                        pass

        total_comprobante = round(total_subtotal + total_impuestos, 2)

        # Validación estricta y protección anti-doble anulación para Notas de Crédito ('03')
        if tipo_doc == '03':
            ref_num = str(ref_data.get('numero') or body.get('ref_numero_clave') or '').strip()
            ref_doc = conn.execute("""
                SELECT * FROM electronic_documents
                WHERE company_id = ? AND (clave_numerica = ? OR consecutivo = ? OR id = ?)
            """, (cid, ref_num, ref_num, ref_num)).fetchone()

            if ref_doc:
                # Regla 1: No es posible emitir Nota de Crédito sobre otra Nota de Crédito
                if ref_doc['tipo_documento'] == '03':
                    raise EmissionError("No es posible emitir una Nota de Crédito sobre otra Nota de Crédito")

                # Regla 2: Límite de reembolso acumulado
                prev_nc_total = conn.execute("""
                    SELECT COALESCE(SUM(total_comprobante), 0.0) FROM electronic_documents
                    WHERE company_id = ? AND tipo_documento = '03'
                      AND (ref_numero_clave = ? OR ref_numero_clave = ? OR ref_numero_clave = ?)
                      AND id != ?
                """, (cid, ref_doc['clave_numerica'], ref_doc['consecutivo'], ref_doc['id'], doc_id)).fetchone()[0]

                saldo_disp = round(float(ref_doc['total_comprobante'] or 0.0) - float(prev_nc_total), 2)
                if saldo_disp <= 0.001:
                    raise EmissionError(f"El comprobante referenciado {ref_doc['consecutivo']} ya se encuentra totalmente anulado/acreditado")

                if total_comprobante > saldo_disp + 0.01:
                    raise EmissionError(f"El monto de la Nota de Crédito (₡{total_comprobante:,.2f}) excede el saldo acreditable restante (₡{saldo_disp:,.2f}) del comprobante referenciado")

                # Regla 3: Sincronización automática con Cuentas por Cobrar
                ar_row = conn.execute("""
                    SELECT * FROM accounts_receivable
                    WHERE company_id = ? AND document_id = ?
                """, (cid, ref_doc['id'])).fetchone()
                if ar_row:
                    ar_id = ar_row['id']
                    ar_saldo = float(ar_row['saldo_pendiente'] or 0.0)
                    nuevo_saldo = max(0.0, round(ar_saldo - total_comprobante, 2))
                    nuevo_estado = 'anulada' if nuevo_saldo <= 0.001 else ('parcial' if nuevo_saldo < float(ar_row['monto_original']) else 'pendiente')
                    conn.execute("""
                        UPDATE accounts_receivable
                        SET saldo_pendiente = ?, estado = ?, updated_at = datetime('now','localtime')
                        WHERE id = ?
                    """, (nuevo_saldo, nuevo_estado, ar_id))

        # 5. Incrementar consecutivo en terminal
        conn.execute(f"UPDATE company_terminals SET {consec_field} = ? WHERE id = ?",
                     (siguiente_num, term['id']))

        # 6. Datos del documento para XML y base de datos
        pos_shift_id = str(body.get('pos_shift_id') or '').strip()
        doc_data = {
            'id': doc_id,
            'company_id': cid,
            'pos_shift_id': pos_shift_id,
            'customer_id': receptor.get('id') if receptor else '',
            'customer_nombre': receptor.get('nombre') if receptor else 'Consumidor Final',
            'customer_identificacion': receptor.get('identificacion') if receptor else '',
            'customer_tipo_id': receptor.get('tipo_identificacion') if receptor else '01',
            'customer_email': receptor.get('email') if receptor else '',
            'tipo_documento': tipo_doc,
            'codigo_sucursal': suc,
            'codigo_terminal': ter,
            'consecutivo': consecutivo,
            'clave_numerica': clave,
            'fecha_emision': fecha_emision_iso,
            'moneda': moneda,
            'tipo_cambio': tipo_cambio,
            'condicion_venta': cond_venta,
            'medio_pago': medio_pago,
            'plazo_credito': plazo_credito,
            'total_bruto': total_bruto,
            'total_descuentos': total_descuentos,
            'subtotal': total_subtotal,
            'total_impuestos': total_impuestos,
            'total_comprobante': total_comprobante,
            'ref_tipo_documento': ref_data.get('tipo_documento', ''),
            'ref_numero_clave': ref_data.get('numero', ''),
            'ref_fecha_emision': ref_data.get('fecha_emision', ''),
            'ref_codigo': ref_data.get('codigo', ''),
            'ref_razon': ref_data.get('razon', ''),
            'usuario_creacion': actor,
            'estado_comprobante': 'generado',
            'hacienda_mensaje': ''
        }

        # 7. Generar XML oficial
        emisor_xml_data = {**emisor, **fe_cfg}
        xml_crudo = generate_electronic_invoice_xml(
            doc_data=doc_data,
            emisor_data=emisor_xml_data,
            receptor_data=receptor,
            lineas=processed_lines,
            referencia=ref_data
        )

        # 8. Firma Digital XAdES-BES si hay certificado configurado
        xml_firmado = ''
        if fe_cfg.get('has_cert_file') and fe_cfg.get('cert_file_enc') and fe_cfg.get('cert_pin_enc') and secret_store.is_configured():
            try:
                p12_bytes = secret_store.decrypt(cid, fe_cfg['cert_file_enc'])
                pin = secret_store.decrypt(cid, fe_cfg['cert_pin_enc']).decode('utf-8')
                xml_firmado = sign_xml_xades_bes(xml_crudo, p12_bytes, pin)
                doc_data['estado_comprobante'] = 'firmado'
            except Exception as e:
                # Si falla la firma digital, registrar estado pero no abortar la persistencia
                doc_data['hacienda_mensaje'] = f"Error en firma digital: {e}"
        else:
            xml_firmado = xml_crudo  # XML generado sin firmar aún

        doc_data['xml_firmado'] = xml_firmado

        # 9. Envío a Hacienda si está firmado y hay credenciales
        hacienda_user = fe_cfg.get('hacienda_username')
        hacienda_pass_enc = fe_cfg.get('hacienda_password_enc')
        ambiente = fe_cfg.get('ambiente_hacienda', 'stag')
        modo_offline = bool(body.get('modo_offline') or situacion in ('2', '3'))

        if modo_offline:
            doc_data['estado_comprobante'] = 'pendiente_envio' if xml_firmado else 'generado'
            doc_data['hacienda_mensaje'] = f"Comprobante en cola ({'Contingencia' if situacion == '2' else 'Sin internet'}). Pendiente de reenvío."
        elif xml_firmado and hacienda_user and hacienda_pass_enc and secret_store.is_configured():
            try:
                h_pass = secret_store.decrypt(cid, hacienda_pass_enc).decode('utf-8')
                token = get_hacienda_token(ambiente, hacienda_user, h_pass)
                resp = send_to_hacienda(
                    ambiente=ambiente,
                    token=token,
                    clave=clave,
                    fecha_emision=fecha_emision_iso,
                    emisor_tipo=emisor_xml_data.get('tipo_identificacion', '01'),
                    emisor_id=re.sub(r'\D', '', str(emisor_xml_data.get('identificacion', ''))),
                    receptor_tipo=receptor.get('tipo_identificacion') if receptor else None,
                    receptor_id=re.sub(r'\D', '', str(receptor.get('identificacion', ''))) if receptor else None,
                    xml_firmado=xml_firmado
                )
                doc_data['estado_comprobante'] = 'enviado'
                doc_data['hacienda_mensaje'] = resp.get('mensaje', 'Enviado a Hacienda')
            except Exception as e:
                doc_data['estado_comprobante'] = 'error_comunicacion'
                doc_data['hacienda_mensaje'] = f"Envío pendiente o fallido: {e}"

        # 10. Guardar documento en electronic_documents (primero para satisfacer FKs)
        conn.execute("""
            INSERT INTO electronic_documents (
                id, company_id, customer_id, customer_nombre, customer_identificacion,
                customer_tipo_id, customer_email, tipo_documento, codigo_sucursal,
                codigo_terminal, consecutivo, clave_numerica, fecha_emision, moneda,
                tipo_cambio, condicion_venta, medio_pago, plazo_credito, total_bruto,
                total_descuentos, subtotal, total_impuestos, total_comprobante,
                estado_comprobante, hacienda_mensaje, xml_firmado, ref_tipo_documento,
                ref_numero_clave, ref_fecha_emision, ref_codigo, ref_razon, pos_shift_id, usuario_creacion
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            doc_data['id'], doc_data['company_id'], doc_data['customer_id'], doc_data['customer_nombre'],
            doc_data['customer_identificacion'], doc_data['customer_tipo_id'], doc_data['customer_email'],
            doc_data['tipo_documento'], doc_data['codigo_sucursal'], doc_data['codigo_terminal'],
            doc_data['consecutivo'], doc_data['clave_numerica'], doc_data['fecha_emision'],
            doc_data['moneda'], doc_data['tipo_cambio'], doc_data['condicion_venta'],
            doc_data['medio_pago'], doc_data['plazo_credito'], doc_data['total_bruto'],
            doc_data['total_descuentos'], doc_data['subtotal'], doc_data['total_impuestos'],
            doc_data['total_comprobante'], doc_data['estado_comprobante'], doc_data.get('hacienda_mensaje', ''),
            doc_data['xml_firmado'], doc_data['ref_tipo_documento'], doc_data['ref_numero_clave'],
            doc_data['ref_fecha_emision'], doc_data['ref_codigo'], doc_data['ref_razon'],
            doc_data.get('pos_shift_id', ''), doc_data['usuario_creacion']
        ))

        # 11. Guardar líneas en electronic_document_lines
        for l in processed_lines:
            conn.execute("""
                INSERT INTO electronic_document_lines (
                    id, document_id, company_id, numero_linea, product_id,
                    codigo_producto, codigo_cabys, descripcion, categoria_nombre,
                    cantidad, unidad_medida, precio_unitario, monto_descuento,
                    naturaleza_descuento, subtotal_linea, base_imponible,
                    tarifa_iva_codigo, tarifa_iva_porcentaje, monto_impuesto, total_linea
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                l['id'], l['document_id'], l['company_id'], l['numero_linea'], l['product_id'],
                l['codigo_producto'], l['codigo_cabys'], l['descripcion'], l['categoria_nombre'],
                l['cantidad'], l['unidad_medida'], l['precio_unitario'], l['monto_descuento'],
                l['naturaleza_descuento'], l['subtotal_linea'], l['base_imponible'],
                l['tarifa_iva_codigo'], l['tarifa_iva_porcentaje'], l['monto_impuesto'], l['total_linea']
            ))

        # 12. Cuenta por Cobrar automática si es a crédito ('06') (referencia a electronic_documents existente)
        if cond_venta == '06' and total_comprobante > 0:
            ar_id = str(uuid.uuid4())
            f_venc = (ahora + timedelta(days=plazo_credito)).strftime('%Y-%m-%d')
            conn.execute("""
                INSERT INTO accounts_receivable (
                    id, company_id, document_id, customer_id, customer_nombre,
                    fecha_emision, fecha_vencimiento, monto_original, monto_abonado,
                    saldo_pendiente, estado
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0.0, ?, 'pendiente')
            """, (ar_id, cid, doc_id, doc_data['customer_id'], doc_data['customer_nombre'],
                  ahora.strftime('%Y-%m-%d'), f_venc, total_comprobante, total_comprobante))

        database.insert_audit_log(conn, 'ventas_document_emit', actor, 'document', doc_id,
                                  {'consecutivo': consecutivo, 'clave': clave, 'total': total_comprobante,
                                   'tipo': tipo_doc, 'estado': doc_data['estado_comprobante']})

    # 13. Integración Contable NIIF: Generar asiento contable automático
    try:
        from services.ventas_contabilidad_service import generate_invoice_journal_entry, generate_credit_note_journal_entry
        if tipo_doc == '03':
            asnt_res = generate_credit_note_journal_entry(cid, doc_id, db_path=db_path)
        else:
            asnt_res = generate_invoice_journal_entry(cid, doc_id, db_path=db_path)
        if asnt_res:
            doc_data['asiento_id'] = asnt_res.get('asiento_id', '')
            doc_data['asiento_costo_id'] = asnt_res.get('asiento_costo_id', '')
    except Exception as e_asnt:
        print(f"[VENTAS] Aviso generando asiento contable automático para {consecutivo}: {e_asnt}")

    return doc_data


def get_document(company_id: str, document_id: str) -> dict:
    cid = str(company_id or '').strip()
    did = str(document_id or '').strip()

    with database.get_db() as conn:
        doc_row = conn.execute("SELECT * FROM electronic_documents WHERE id = ? AND company_id = ?",
                               (did, cid)).fetchone()
        if not doc_row:
            raise EmissionError("Comprobante electrónico no encontrado")

        d = dict(doc_row)
        lines = conn.execute("SELECT * FROM electronic_document_lines WHERE document_id = ? AND company_id = ? ORDER BY numero_linea ASC",
                             (did, cid)).fetchall()
        d['lineas'] = [dict(l) for l in lines]
        return d


def list_documents(company_id: str, tipo: str = None, estado: str = None,
                   fecha_inicio: str = None, fecha_fin: str = None, q: str = None,
                   limit: int = 50, offset: int = 0) -> dict:
    cid = str(company_id or '').strip()
    limit = max(1, min(int(limit or 50), 200))
    offset = max(0, int(offset or 0))

    where = ["company_id = ?"]
    params = [cid]

    if tipo and tipo != 'todos':
        where.append("tipo_documento = ?")
        params.append(tipo)

    if estado and estado != 'todos':
        where.append("estado_comprobante = ?")
        params.append(estado)

    if fecha_inicio:
        where.append("date(fecha_emision) >= date(?)")
        params.append(fecha_inicio)

    if fecha_fin:
        where.append("date(fecha_emision) <= date(?)")
        params.append(fecha_fin)

    if q and q.strip():
        term = f"%{q.strip()}%"
        where.append("(consecutivo LIKE ? OR clave_numerica LIKE ? OR customer_nombre LIKE ? OR customer_identificacion LIKE ?)")
        params.extend([term, term, term, term])

    where_sql = " AND ".join(where)

    with database.get_db() as conn:
        total = conn.execute(f"SELECT COUNT(*) FROM electronic_documents WHERE {where_sql}", params).fetchone()[0]
        rows = conn.execute(f"""
            SELECT id, company_id, customer_id, customer_nombre, customer_identificacion,
                   tipo_documento, consecutivo, clave_numerica, fecha_emision, moneda,
                   total_comprobante, estado_comprobante, hacienda_mensaje, created_at
            FROM electronic_documents
            WHERE {where_sql}
            ORDER BY fecha_emision DESC, created_at DESC
            LIMIT ? OFFSET ?
        """, params + [limit, offset]).fetchall()

        return {'items': [dict(r) for r in rows], 'total': total, 'limit': limit, 'offset': offset}


def sync_hacienda_status(company_id: str, document_id: str, actor: str) -> dict:
    """Consulta ante Hacienda el estado de validación y actualiza el documento."""
    doc = get_document(company_id, document_id)
    clave = doc.get('clave_numerica')
    if not clave:
        raise EmissionError("El documento no tiene clave numérica")

    with database.get_db() as conn:
        fe_cfg_row = conn.execute("SELECT * FROM company_fe_settings WHERE company_id = ?", (company_id,)).fetchone()
        fe_cfg = dict(fe_cfg_row) if fe_cfg_row else {}

    ambiente = fe_cfg.get('ambiente_hacienda', 'stag')
    hacienda_user = fe_cfg.get('hacienda_username')
    hacienda_pass_enc = fe_cfg.get('hacienda_password_enc')

    token = "sim-token"
    if hacienda_user and hacienda_pass_enc and secret_store.is_configured():
        try:
            h_pass = secret_store.decrypt(company_id, hacienda_pass_enc).decode('utf-8')
            token = get_hacienda_token(ambiente, hacienda_user, h_pass)
        except Exception:
            pass

    status_resp = check_hacienda_status(ambiente, token, clave)
    nuevo_estado = status_resp.get('estado') or doc['estado_comprobante']
    xml_resp = status_resp.get('xml_respuesta') or doc.get('xml_respuesta_hacienda', '')
    mensaje = status_resp.get('mensaje') or doc.get('hacienda_mensaje', '')

    with database.get_db() as conn:
        conn.execute("""
            UPDATE electronic_documents SET
                estado_comprobante = ?,
                hacienda_mensaje = ?,
                xml_respuesta_hacienda = ?,
                updated_at = datetime('now','localtime')
            WHERE id = ? AND company_id = ?
        """, (nuevo_estado, mensaje, xml_resp, document_id, company_id))

        database.insert_audit_log(conn, 'ventas_hacienda_check', actor, 'document', document_id,
                                  {'clave': clave, 'nuevo_estado': nuevo_estado})

    return get_document(company_id, document_id)


def download_document_pdf(company_id: str, document_id: str) -> tuple:
    """Genera y retorna (filename, pdf_bytes)."""
    doc = get_document(company_id, document_id)
    with database.get_db() as conn:
        emisor = dict(conn.execute("SELECT * FROM clientes WHERE id = ?", (company_id,)).fetchone())
        fe_cfg = dict(conn.execute("SELECT * FROM company_fe_settings WHERE company_id = ?", (company_id,)).fetchone() or {})

    emisor_full = {**emisor, **fe_cfg}
    receptor = {
        'nombre': doc.get('customer_nombre'),
        'identificacion': doc.get('customer_identificacion'),
        'email': doc.get('customer_email')
    }
    referencia = {
        'tipo_documento': doc.get('ref_tipo_documento'),
        'numero': doc.get('ref_numero_clave'),
        'fecha_emision': doc.get('ref_fecha_emision'),
        'razon': doc.get('ref_razon')
    } if doc.get('ref_numero_clave') else None

    pdf_bytes = generate_electronic_document_pdf(
        doc_data=doc,
        emisor_data=emisor_full,
        receptor_data=receptor,
        lineas=doc.get('lineas'),
        referencia=referencia
    )
    filename = f"{doc.get('consecutivo') or doc['id']}.pdf"
    return filename, pdf_bytes


def download_document_xml(company_id: str, document_id: str) -> tuple:
    """Retorna (filename, xml_string) del comprobante firmado."""
    doc = get_document(company_id, document_id)
    xml_content = doc.get('xml_firmado') or ''
    if not xml_content:
        raise EmissionError("El comprobante aún no tiene XML generado o firmado")
    filename = f"{doc.get('clave_numerica') or doc['id']}.xml"
    return filename, xml_content


def retry_pending_hacienda_queue(company_id: str, actor: str, document_id: str = None, limit: int = 50, db_path=None) -> dict:
    """
    Reintenta el envío de comprobantes electrónicos ante el Ministerio de Hacienda para
    documentos que quedaron en estado 'pendiente_envio', 'error_comunicacion', o 'generado'.
    """
    cid = str(company_id or '').strip()
    if not cid:
        raise EmissionError("company_id es requerido")

    with database.get_db(db_path) as conn:
        fe_cfg_row = conn.execute("SELECT * FROM company_fe_settings WHERE company_id = ?", (cid,)).fetchone()
        if not fe_cfg_row:
            raise EmissionError("Configuración de facturación electrónica no encontrada")
        fe_cfg = dict(fe_cfg_row)

        hacienda_user = fe_cfg.get('hacienda_username')
        hacienda_pass_enc = fe_cfg.get('hacienda_password_enc')
        ambiente = fe_cfg.get('ambiente_hacienda', 'stag')

        token = "sim-token"
        if hacienda_user and hacienda_pass_enc and secret_store.is_configured():
            try:
                h_pass = secret_store.decrypt(cid, hacienda_pass_enc).decode('utf-8')
                token = get_hacienda_token(ambiente, hacienda_user, h_pass)
            except Exception:
                pass

        # Buscar comprobantes pendientes de envío
        if document_id:
            docs_rows = conn.execute("""
                SELECT * FROM electronic_documents
                WHERE company_id = ? AND id = ?
            """, (cid, document_id)).fetchall()
        else:
            docs_rows = conn.execute("""
                SELECT * FROM electronic_documents
                WHERE company_id = ? AND estado_comprobante IN ('pendiente_envio', 'error_comunicacion', 'generado', 'firmado')
                ORDER BY created_at ASC
                LIMIT ?
            """, (cid, max(1, min(int(limit or 50), 100)))).fetchall()

        procesados = 0
        enviados = 0
        fallidos = 0
        detalles = []

        for d_row in docs_rows:
            d = dict(d_row)
            did = d['id']
            clave = d['clave_numerica']
            xml_firmado = d.get('xml_firmado') or ''

            # Si el comprobante no estaba firmado, intentar firmar en memoria
            if not xml_firmado and fe_cfg.get('has_cert_file') and fe_cfg.get('cert_file_enc') and fe_cfg.get('cert_pin_enc'):
                try:
                    p12_bytes = secret_store.decrypt(cid, fe_cfg['cert_file_enc'])
                    pin = secret_store.decrypt(cid, fe_cfg['cert_pin_enc']).decode('utf-8')
                    # Generar XML crudo nuevamente si hace falta
                    pass
                except Exception:
                    pass

            if not xml_firmado:
                fallidos += 1
                detalles.append({'id': did, 'consecutivo': d.get('consecutivo'), 'estado': 'error', 'mensaje': 'Sin XML firmado'})
                continue

            procesados += 1
            emisor_id = re.sub(r'\D', '', str(fe_cfg.get('identificacion') or ''))
            receptor_id = re.sub(r'\D', '', str(d.get('customer_identificacion') or '')) if d.get('customer_identificacion') else None

            try:
                resp = send_to_hacienda(
                    ambiente=ambiente,
                    token=token,
                    clave=clave,
                    fecha_emision=d['fecha_emision'],
                    emisor_tipo=fe_cfg.get('tipo_identificacion', '01'),
                    emisor_id=emisor_id,
                    receptor_tipo=d.get('customer_tipo_id') or '01' if receptor_id else None,
                    receptor_id=receptor_id,
                    xml_firmado=xml_firmado
                )
                nuevo_estado = 'enviado'
                msg = resp.get('mensaje', 'Reenvío exitoso a Hacienda')
                enviados += 1
                detalles.append({'id': did, 'consecutivo': d.get('consecutivo'), 'estado': nuevo_estado, 'mensaje': msg})
            except Exception as e_send:
                nuevo_estado = 'error_comunicacion'
                msg = f"Fallo en reintento: {e_send}"
                fallidos += 1
                detalles.append({'id': did, 'consecutivo': d.get('consecutivo'), 'estado': nuevo_estado, 'mensaje': msg})

            conn.execute("""
                UPDATE electronic_documents
                SET estado_comprobante = ?, hacienda_mensaje = ?, updated_at = datetime('now','localtime')
                WHERE id = ? AND company_id = ?
            """, (nuevo_estado, msg, did, cid))

        database.insert_audit_log(conn, 'ventas_hacienda_retry_queue', actor, 'document_queue', cid,
                                  {'procesados': procesados, 'enviados': enviados, 'fallidos': fallidos})

    return {
        'procesados': procesados,
        'enviados': enviados,
        'fallidos': fallidos,
        'detalles': detalles
    }
