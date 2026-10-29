"""
FACTURAMI - Servicio de Contabilidad NIIF para Pymes
Lógica de partida doble, libro diario, conciliación histórica y depreciación.
"""
import uuid
import datetime
import json
import db as database

def get_compras_sin_contrapartida(cliente_id, db_path=None):
    """
    Lista compras, gastos o activos registrados que carecen de contrapartida o asiento contable.
    """
    with database.get_db(db_path) as conn:
        # Obtener compras y gastos sin asiento_id o con origen_fondos pendiente
        rows = conn.execute("""
            SELECT id, modulo, fecha, documento, tipo_doc, descripcion, proveedor, total_factura,
                   monto_ns, monto_ex, monto_13, monto_8, monto_4, monto_2, monto_1,
                   origen_fondos, asiento_id
            FROM transacciones
            WHERE cliente_id=? AND modulo IN ('compras','gastos','costos')
              AND (asiento_id IS NULL OR asiento_id = '' OR origen_fondos IS NULL OR origen_fondos = '')
            ORDER BY fecha ASC
        """, (cliente_id,)).fetchall()
        
        compras_huerfanas = [dict(r) for r in rows]
        
        # Revisar también activos fijos registrados en configuracion activos_{cid}
        activos_str = database.config_get(conn, f'activos_{cliente_id}', '{}')
        activos_data = json.loads(activos_str) if activos_str else {}
        no_cor = activos_data.get('no_cor', {})
        
        activos_huerfanos = []
        for cat_name, cat_items in no_cor.items():
            if isinstance(cat_items, dict):
                for item_key, item_val in cat_items.items():
                    # Si es un activo con costo pero sin asiento
                    if isinstance(item_val, dict) and (item_val.get('costo') or item_val.get('monto')):
                        monto = float(item_val.get('costo') or item_val.get('monto') or 0)
                        activos_huerfanos.append({
                            'id': f"activo_{cat_name}_{item_key}",
                            'modulo': 'activos',
                            'fecha': item_val.get('fecha', '2024-01-01'),
                            'documento': item_val.get('doc', item_key),
                            'tipo_doc': 'Activo Fijo',
                            'descripcion': f"Activo Fijo: {cat_name} - {item_val.get('nombre', item_key)}",
                            'proveedor': item_val.get('prov', 'Aporte o Adquisición'),
                            'total_factura': monto,
                            'origen_fondos': item_val.get('origen_fondos', '')
                        })
                        
        return {
            'cliente_id': cliente_id,
            'compras_sin_contrapartida': compras_huerfanas,
            'activos_sin_contrapartida': activos_huerfanos,
            'total_documentos': len(compras_huerfanas) + len(activos_huerfanos)
        }

def reconciliar_historico(cliente_id, asignaciones, db_path=None):
    """
    Asigna contrapartida a documentos históricos y genera los asientos contables correspondientes.
    Trazable e idempotente.
    """
    resultados = []
    with database.get_db(db_path) as conn:
        for asig in asignaciones:
            doc_id = asig.get('id')
            modulo = asig.get('modulo', 'compras')
            origen = asig.get('origen_fondos', 'banco') # 'banco', 'caja', 'credito', 'aporte_propietario'
            cuenta_credito = asig.get('cuenta_credito', '1.1.1.02.01')
            nombre_credito = asig.get('nombre_credito', 'Banco Nacional de Costa Rica (BNCR)')
            
            if origen == 'aporte_propietario':
                cuenta_credito = '3.1.1.01'
                nombre_credito = 'Capital del Propietario (Aportes)'
            elif origen == 'caja':
                cuenta_credito = '1.1.1.01'
                nombre_credito = 'Caja General'
            elif origen == 'credito':
                cuenta_credito = '2.1.1.01'
                nombre_credito = 'Cuentas por Pagar Proveedores'
            elif origen == 'tarjeta':
                cuenta_credito = '2.1.2.01'
                nombre_credito = 'Tarjetas de Crédito por Pagar'
                
            fecha = asig.get('fecha', datetime.date.today().isoformat())
            monto_total = round(float(asig.get('total_factura', 0) or 0), 2)
            if monto_total <= 0:
                monto_total = round(sum(float(asig.get(k, 0) or 0) for k in ('monto_ns', 'monto_ex', 'monto_05', 'monto_1', 'monto_2', 'monto_4', 'monto_8', 'monto_13')), 2)
            doc_ref = asig.get('documento', 'HIST')
            desc = asig.get('descripcion', 'Reconciliación histórica')
            
            # Idempotencia: Verificar si ya existe asiento con este hash_origen
            hash_origen = f"REC_{cliente_id}_{doc_ref}_{fecha}_{monto_total}"
            exist = conn.execute("SELECT id FROM asientos WHERE cliente_id=? AND hash_origen=? AND estado='valido'", (cliente_id, hash_origen)).fetchone()
            if exist:
                resultados.append({
                    'documento': doc_ref,
                    'asiento_id': exist['id'],
                    'debito': monto_total,
                    'credito': monto_total,
                    'cuenta_debito': '1.2.1.01',
                    'cuenta_credito': cuenta_credito,
                    'origen': origen,
                    'idempotente': True
                })
                continue

            # Determinar cuenta débito
            if modulo == 'activos' or 'VEHICULOS' in desc.upper():
                cuenta_debito = '1.2.1.01'
                nombre_debito = 'Vehículos'
            elif 'MAQUINARIA' in desc.upper():
                cuenta_debito = '1.2.1.02'
                nombre_debito = 'Maquinaria y Herramientas'
            elif modulo == 'gastos':
                cuenta_debito = '5.2.1.03'
                nombre_debito = 'Gastos Administrativos Generales'
            else:
                cuenta_debito = '5.1.1.01'
                nombre_debito = 'Costos de Operación y Compras de Materiales'
                
            if monto_total <= 0:
                continue
                
            # Generar asiento contable de partida doble
            asiento_dict = {
                'cliente_id': cliente_id,
                'fecha_contable': fecha,
                'tipo_operacion': 'reconciliacion_compra',
                'documento_origen': str(doc_ref),
                'modulo_origen': modulo,
                'concepto': f"Reconciliación: {desc} (Ref {doc_ref})",
                'hash_origen': f"REC_{cliente_id}_{doc_ref}_{fecha}_{monto_total}"
            }
            
            lineas = [
                {
                    'cuenta_codigo': cuenta_debito,
                    'cuenta_nombre': nombre_debito,
                    'debito': monto_total,
                    'credito': 0.0,
                    'referencia_doc': str(doc_ref)
                },
                {
                    'cuenta_codigo': cuenta_credito,
                    'cuenta_nombre': nombre_credito,
                    'debito': 0.0,
                    'credito': monto_total,
                    'referencia_doc': str(doc_ref)
                }
            ]
            
            aid = database.insert_asiento(conn, asiento_dict, lineas)
            
            # Actualizar fila en transacciones si existe
            if doc_id and not str(doc_id).startswith('activo_'):
                conn.execute("""
                    UPDATE transacciones 
                    SET origen_fondos=?, asiento_id=? 
                    WHERE id=? AND cliente_id=?
                """, (origen, aid, doc_id, cliente_id))
                
            resultados.append({
                'documento': doc_ref,
                'asiento_id': aid,
                'debito': monto_total,
                'credito': monto_total,
                'cuenta_debito': cuenta_debito,
                'cuenta_credito': cuenta_credito,
                'origen': origen
            })
            
    return {'cliente_id': cliente_id, 'asientos_generados': len(resultados), 'detalle': resultados}

def simular_recalculo_depreciacion(cliente_id, fecha_corte_str='2026-10-31', db_path=None):
    """
    Calcula la depreciación acumulada exacta mes a mes para cada activo desde su adquisición hasta la fecha de corte.
    Verifica que la depreciación acumulada nunca supere el costo menos el valor residual.
    """
    with database.get_db(db_path) as conn:
        activos_str = database.config_get(conn, f'activos_{cliente_id}', '{}')
        activos_data = json.loads(activos_str) if activos_str else {}
        no_cor = activos_data.get('no_cor', {})
        
        try:
            corte = datetime.datetime.strptime(fecha_corte_str, "%Y-%m-%d").date()
        except Exception:
            corte = datetime.date(2026, 10, 31)
            
        reporte = []
        tot_costo = 0.0
        tot_depre_recalculada = 0.0
        tot_valor_neto = 0.0
        
        # Mapeo de vidas útiles por categoría según DGT / NIIF Pymes
        VIDAS_UTILES = {
            'VEHICULOS': 60, # 5 años
            'MAQUINARIA': 60, # 5 años
            'EQUIPO': 36, # 3 años
            'EDIFICIOS': 240 # 20 años
        }
        
        for cat_name, cat_obj in no_cor.items():
            if not isinstance(cat_obj, dict):
                continue
            cat_upper = cat_name.upper()
            meses_vida = 60
            for k, v in VIDAS_UTILES.items():
                if k in cat_upper:
                    meses_vida = v
                    break
                    
            # Obtener lista de movimientos del activo
            movs = cat_obj.get('movs', [])
            if not isinstance(movs, list) or len(movs) == 0:
                # Si el objeto mismo es el activo
                movs = [cat_obj]
                    
            for idx, item_val in enumerate(movs):
                if not isinstance(item_val, dict):
                    continue
                costo = float(item_val.get('costo') or item_val.get('monto') or 0)
                if costo <= 0:
                    continue
                    
                val_residual = float(item_val.get('residual') or item_val.get('val_residual') or 0)
                monto_depreciable = max(0.0, costo - val_residual)
                
                # Cuota mensual
                dep_m_custom = float(item_val.get('depMensual') or 0)
                cuota_mensual = round(dep_m_custom if dep_m_custom > 0 else (monto_depreciable / meses_vida), 2)
                
                fecha_adq_str = item_val.get('fecha', '2024-01-01')
                try:
                    fecha_adq = datetime.datetime.strptime(fecha_adq_str, "%Y-%m-%d").date()
                except Exception:
                    fecha_adq = datetime.date(2024, 1, 1)
                    
                # Calcular meses transcurridos
                if corte >= fecha_adq:
                    meses_transcurridos = (corte.year - fecha_adq.year) * 12 + (corte.month - fecha_adq.month)
                else:
                    meses_transcurridos = 0
                    
                meses_a_depreciar = min(meses_transcurridos, meses_vida)
                depre_acum = round(min(monto_depreciable, cuota_mensual * meses_a_depreciar), 2)
                valor_neto = round(costo - depre_acum, 2)
                
                # Desglose: Depreciación años anteriores vs ejercicio actual (2026)
                meses_ant = max(0, min(meses_a_depreciar, ((2025 - fecha_adq.year) * 12 + (12 - fecha_adq.month) + 1))) if fecha_adq.year < 2026 else 0
                depre_ant = round(min(monto_depreciable, cuota_mensual * meses_ant), 2)
                depre_actual = round(depre_acum - depre_ant, 2)
                
                tot_costo += costo
                tot_depre_recalculada += depre_acum
                tot_valor_neto += valor_neto
                
                reporte.append({
                    'categoria': cat_name,
                    'nombre': item_val.get('det') or item_val.get('nombre') or f"{cat_name} #{idx+1}",
                    'fecha_adquisicion': fecha_adq_str,
                    'costo_historico': costo,
                    'valor_residual': val_residual,
                    'vida_util_meses': meses_vida,
                    'meses_transcurridos': meses_transcurridos,
                    'cuota_mensual': cuota_mensual,
                    'depreciacion_acumulada': depre_acum,
                    'depreciacion_ejercicios_anteriores': depre_ant,
                    'depreciacion_ejercicio_actual': depre_actual,
                    'valor_neto_en_libros': valor_neto
                })
                
        return {
            'cliente_id': cliente_id,
            'fecha_corte': fecha_corte_str,
            'totales': {
                'costo_historico': round(tot_costo, 2),
                'depreciacion_acumulada': round(tot_depre_recalculada, 2),
                'valor_neto': round(tot_valor_neto, 2)
            },
            'activos': reporte
        }
