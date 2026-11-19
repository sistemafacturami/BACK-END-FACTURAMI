"""
FACTURAMI - Pruebas de Integración Contable con NIIF y Asientos Automáticos (Fase 7)
====================================================================================
Verifica:
  1. Mapeo contable por empresa y precarga de catálogo estándar NIIF.
  2. Actualización de mapeo de cuentas contables.
  3. Generación automática de asientos para ventas de contado con partida doble estricta.
  4. Generación automática de asientos para ventas a crédito vinculadas a CxC.
  5. Asientos automáticos de Costo de Ventas e Inventario a costo promedio ponderado.
  6. Asientos de Nota de Crédito (anulación, reversión comercial y de existencias).
  7. Asientos automáticos para abonos y cancelaciones de Cuentas por Cobrar (CxC).
  8. Asientos automáticos para ventas POS (mostrador) y movimientos de efectivo.
  9. Flag de auto-generación y generación manual a demanda vía API.
  10. Seguridad por roles (contador/admin_empresa) y estricto aislamiento multiempresa.
"""
import os
import sys
import tempfile
import unittest
from datetime import date, timedelta
from flask_jwt_extended import create_access_token

sys.path.insert(0, os.path.abspath('.'))

from app import create_app
import db as database
import services.ventas_service as ventas_service
import services.ventas_contabilidad_service as contabilidad_service


class TestFase7VentasContabilidad(unittest.TestCase):

    def setUp(self):
        fd, self.tmp_db = tempfile.mkstemp(suffix='.db')
        os.close(fd)
        self.prev_db_path = database.DB_PATH
        database.DB_PATH = self.tmp_db
        database.init_db(self.tmp_db)
        self.app = create_app()
        self.c = self.app.test_client()

        with database.get_db(self.tmp_db) as conn:
            for u in ('owner_a', 'owner_b', 'contador_alfa', 'cajero_alfa'):
                database.upsert_cuenta(conn, {'username': u, 'nombre': u, 'role': 'contador', 'activo': 1})
            database.upsert_cliente(conn, {'id': '3101111111', 'type': 'PJ', 'name': 'Corporación Alfa S.A.',
                                           'mail': 'alfa@empresa.cr'}, cuenta_id='owner_a')
            database.upsert_cliente(conn, {'id': '3102222222', 'type': 'PJ', 'name': 'Corporación Beta S.A.',
                                           'mail': 'beta@empresa.cr'}, cuenta_id='owner_b')

        with self.app.app_context():
            tok = lambda u, r='contador': {'Authorization': 'Bearer ' + create_access_token(
                identity=u, additional_claims={'role': r})}
            self.h_a = tok('owner_a')
            self.h_b = tok('owner_b')
            self.h_con = tok('contador_alfa')
            self.h_caj = tok('cajero_alfa')

        # Asignar roles internos en Alfa
        self.c.post('/api/ventas/users', headers=self.h_a,
                    json={'company_id': '3101111111', 'username': 'contador_alfa', 'role': 'contador'})
        self.c.post('/api/ventas/users', headers=self.h_a,
                    json={'company_id': '3101111111', 'username': 'cajero_alfa', 'role': 'cajero'})

        # Inicializar contexto
        self.c.get('/api/ventas/context?company_id=3101111111', headers=self.h_a)
        self.c.get('/api/ventas/context?company_id=3102222222', headers=self.h_b)

    def tearDown(self):
        database.DB_PATH = self.prev_db_path
        try:
            os.remove(self.tmp_db)
        except OSError:
            pass

    # ── 1. Mapeo Contable Default y Catálogo Estándar ───────────────
    def test_01_mapeo_contable_default_y_catalogo_estandar(self):
        r = self.c.get('/api/ventas/accounting/mapping?company_id=3101111111', headers=self.h_a)
        self.assertEqual(r.status_code, 200)
        data = r.get_json()['data']
        self.assertEqual(data['company_id'], '3101111111')
        self.assertEqual(data['cuenta_caja_efectivo'], '1.1.1.01.01')
        self.assertEqual(data['cuenta_cxc_clientes'], '1.1.2.01')
        self.assertEqual(data['cuenta_iva_por_pagar'], '2.1.3.01')
        self.assertEqual(data['cuenta_ventas_ingresos'], '4.1.1.01')
        self.assertEqual(data['cuenta_costo_ventas'], '5.1.1.01')
        self.assertEqual(data['auto_generar_asientos'], 1)

        # Verificar que el catálogo de cuentas de la empresa tenga las cuentas estándar NIIF
        r_cat = self.c.get('/api/ventas/accounting/catalog?company_id=3101111111', headers=self.h_a)
        self.assertEqual(r_cat.status_code, 200)
        cuentas = r_cat.get_json()['data']
        codigos = [c['codigo'] for c in cuentas]
        self.assertIn('1.1.1.01.01', codigos)
        self.assertIn('1.1.2.01', codigos)
        self.assertIn('2.1.3.01', codigos)
        self.assertIn('4.1.1.01', codigos)
        self.assertIn('5.1.1.01', codigos)

    # ── 2. Actualizar Mapeo Contable Personalizado ──────────────────
    def test_02_actualizar_mapeo_contable_personalizado(self):
        r = self.c.put('/api/ventas/accounting/mapping', headers=self.h_con, json={
            'company_id': '3101111111',
            'cuenta_caja_efectivo': '1.1.1.01.99',
            'cuenta_ventas_ingresos': '4.1.1.01.05',
            'auto_generar_asientos': 1
        })
        self.assertEqual(r.status_code, 200)
        updated = r.get_json()['data']
        self.assertEqual(updated['cuenta_caja_efectivo'], '1.1.1.01.99')
        self.assertEqual(updated['cuenta_ventas_ingresos'], '4.1.1.01.05')

    # ── 3. Asiento de Venta Contado con Partida Doble Estricta ──────
    def test_03_asiento_venta_contado_partida_doble(self):
        # Emitir factura al contado por 10 000 + 13% IVA = 11 300
        r_emit = self.c.post('/api/ventas/documents', headers=self.h_a, json={
            'company_id': '3101111111',
            'tipo_documento': '01',
            'condicion_venta': '01', # Contado
            'medio_pago': '01',      # Efectivo
            'lineas': [{
                'descripcion': 'Servicio Profesional Contable',
                'codigo_cabys': '1234567890123',
                'cantidad': 1,
                'precio_unitario': 10000.0,
                'tarifa_iva_codigo': '08',
                'tarifa_iva_porcentaje': 13.0
            }]
        })
        self.assertEqual(r_emit.status_code, 200)
        doc = r_emit.get_json()['data']
        self.assertTrue(doc.get('asiento_id'))

        # Consultar los asientos vinculados al comprobante
        r_asnt = self.c.get(f"/api/ventas/accounting/entries?company_id=3101111111&document_id={doc['id']}", headers=self.h_a)
        self.assertEqual(r_asnt.status_code, 200)
        asientos = r_asnt.get_json()['data']
        self.assertEqual(len(asientos), 1)

        asiento_venta = asientos[0]
        self.assertEqual(asiento_venta['id'], doc['asiento_id'])
        self.assertEqual(asiento_venta['tipo_operacion'], 'venta_contado')

        # Verificar estricta partida doble: Debe == Haber == 11300
        self.assertAlmostEqual(asiento_venta['total_debe'], 11300.0, places=2)
        self.assertAlmostEqual(asiento_venta['total_haber'], 11300.0, places=2)

        lineas = asiento_venta['lineas']
        # Débito a Caja (1.1.1.01.01) por 11 300
        deb_caja = next(l for l in lineas if l['cuenta_codigo'] == '1.1.1.01.01')
        self.assertAlmostEqual(deb_caja['debito'], 11300.0, places=2)
        self.assertEqual(deb_caja['credito'], 0.0)

        # Crédito a Ingresos (4.1.1.01) por 10 000
        cre_ing = next(l for l in lineas if l['cuenta_codigo'] == '4.1.1.01')
        self.assertAlmostEqual(cre_ing['credito'], 10000.0, places=2)

        # Crédito a IVA por Pagar (2.1.3.01) por 1 300
        cre_iva = next(l for l in lineas if l['cuenta_codigo'] == '2.1.3.01')
        self.assertAlmostEqual(cre_iva['credito'], 1300.0, places=2)

    # ── 4. Asiento de Venta a Crédito y Creación de CxC ─────────────
    def test_04_asiento_venta_credito_y_cxc(self):
        # Registrar cliente
        r_cli = self.c.post('/api/ventas/customers', headers=self.h_a, json={
            'company_id': '3101111111',
            'tipo_identificacion': '02',
            'identificacion': '3101999999',
            'nombre': 'Cliente Crédito S.A.',
            'condicion_venta_default': '06',
            'plazo_credito_dias': 30
        })
        cust_id = r_cli.get_json()['data']['id']

        # Emitir factura a crédito (condición '06')
        r_emit = self.c.post('/api/ventas/documents', headers=self.h_a, json={
            'company_id': '3101111111',
            'customer_id': cust_id,
            'tipo_documento': '01',
            'condicion_venta': '06', # Crédito
            'plazo_credito': 30,
            'lineas': [{
                'descripcion': 'Consultoría Empresarial',
                'codigo_cabys': '1234567890123',
                'cantidad': 1,
                'precio_unitario': 20000.0,
                'tarifa_iva_codigo': '08',
                'tarifa_iva_porcentaje': 13.0
            }]
        })
        self.assertEqual(r_emit.status_code, 200)
        doc = r_emit.get_json()['data']

        # Verificar asiento contable de crédito
        r_asnt = self.c.get(f"/api/ventas/accounting/entries?company_id=3101111111&document_id={doc['id']}", headers=self.h_a)
        asientos = r_asnt.get_json()['data']
        self.assertEqual(len(asientos), 1)
        asiento = asientos[0]
        self.assertEqual(asiento['tipo_operacion'], 'venta_credito')
        self.assertAlmostEqual(asiento['total_debe'], 22600.0, places=2)
        self.assertAlmostEqual(asiento['total_haber'], 22600.0, places=2)

        # Débito debe ser a CxC Clientes (1.1.2.01)
        deb_cxc = next(l for l in asiento['lineas'] if l['cuenta_codigo'] == '1.1.2.01')
        self.assertAlmostEqual(deb_cxc['debito'], 22600.0, places=2)

        # Verificar que la cuenta por cobrar exista
        r_cxc = self.c.get('/api/ventas/cxc?company_id=3101111111', headers=self.h_a)
        self.assertEqual(r_cxc.status_code, 200)
        items_cxc = r_cxc.get_json()['data']['items']
        self.assertTrue(any(it['document_id'] == doc['id'] and it['monto_original'] == 22600.0 for it in items_cxc))

    # ── 5. Asiento de Costo de Ventas e Inventario a Costo Promedio ─
    def test_05_asiento_costo_ventas_e_inventario_automatico(self):
        # Crear producto con control de stock y costo promedio
        r_prod = self.c.post('/api/ventas/products', headers=self.h_a, json={
            'company_id': '3101111111',
            'codigo_interno': 'PROD-KDX',
            'codigo_cabys': '1234567890123',
            'nombre': 'Disco Duro SSD 1TB',
            'tipo': 'mercancia',
            'precio_compra': 25000.0,
            'precio_venta': 45000.0,
            'tarifa_iva_codigo': '08',
            'existencia': 10,
            'stock_minimo': 2,
            'controla_inventario': 1
        })
        self.assertEqual(r_prod.status_code, 200)
        pid = r_prod.get_json()['data']['id']

        # Registrar entrada en Kardex con costo unitario de 25 000
        self.c.post(f'/api/ventas/products/{pid}/kardex', headers=self.h_a, json={
            'company_id': '3101111111',
            'tipo_movimiento': 'entrada',
            'cantidad': 10,
            'costo_unitario': 25000.0,
            'motivo_ajuste': 'Compra inicial lote 1'
        })

        # Emitir factura vendiendo 2 unidades (Venta: 2 * 45000 = 90000 + IVA; Costo: 2 * 25000 = 50000)
        r_emit = self.c.post('/api/ventas/documents', headers=self.h_a, json={
            'company_id': '3101111111',
            'tipo_documento': '01',
            'condicion_venta': '01',
            'medio_pago': '01',
            'lineas': [{
                'product_id': pid,
                'descripcion': 'Disco Duro SSD 1TB',
                'codigo_cabys': '1234567890123',
                'cantidad': 2,
                'precio_unitario': 45000.0,
                'tarifa_iva_codigo': '08',
                'tarifa_iva_porcentaje': 13.0
            }]
        })
        self.assertEqual(r_emit.status_code, 200)
        doc = r_emit.get_json()['data']
        self.assertTrue(doc.get('asiento_id'))
        self.assertTrue(doc.get('asiento_costo_id'))

        # Consultar ambos asientos vinculados (Venta y Costo de Ventas)
        r_asnt = self.c.get(f"/api/ventas/accounting/entries?company_id=3101111111&document_id={doc['id']}", headers=self.h_a)
        asientos = r_asnt.get_json()['data']
        self.assertEqual(len(asientos), 2)

        # Encontrar asiento de costo
        asnt_costo = next(a for a in asientos if a['tipo_operacion'] == 'costo_venta')
        self.assertAlmostEqual(asnt_costo['total_debe'], 50000.0, places=2)
        self.assertAlmostEqual(asnt_costo['total_haber'], 50000.0, places=2)

        # Débito a Costo de Ventas (5.1.1.01) y Crédito a Inventario (1.1.3.01)
        deb_costo = next(l for l in asnt_costo['lineas'] if l['cuenta_codigo'] == '5.1.1.01')
        cre_inv = next(l for l in asnt_costo['lineas'] if l['cuenta_codigo'] == '1.1.3.01')
        self.assertAlmostEqual(deb_costo['debito'], 50000.0, places=2)
        self.assertAlmostEqual(cre_inv['credito'], 50000.0, places=2)

    # ── 6. Asiento de Nota de Crédito (Anulación y Reversión) ────────
    def test_06_asiento_nota_credito_anulacion_y_reversion(self):
        # Emitir factura original
        r_fac = self.c.post('/api/ventas/documents', headers=self.h_a, json={
            'company_id': '3101111111',
            'tipo_documento': '01',
            'condicion_venta': '01',
            'lineas': [{
                'descripcion': 'Equipo de red',
                'codigo_cabys': '1234567890123',
                'cantidad': 1,
                'precio_unitario': 50000.0,
                'tarifa_iva_codigo': '08',
                'tarifa_iva_porcentaje': 13.0
            }]
        })
        fac = r_fac.get_json()['data']

        # Emitir Nota de Crédito anulando la factura original
        r_nc = self.c.post('/api/ventas/documents', headers=self.h_a, json={
            'company_id': '3101111111',
            'tipo_documento': '03', # Nota de Crédito
            'ref_tipo_documento': '01',
            'ref_numero_clave': fac['clave_numerica'],
            'ref_fecha_emision': fac['fecha_emision'],
            'ref_codigo': '01', # Anula documento
            'ref_razon': 'Devolución de mercadería',
            'lineas': [{
                'descripcion': 'Equipo de red (Anulación)',
                'codigo_cabys': '1234567890123',
                'cantidad': 1,
                'precio_unitario': 50000.0,
                'tarifa_iva_codigo': '08',
                'tarifa_iva_porcentaje': 13.0
            }]
        })
        self.assertEqual(r_nc.status_code, 200)
        nc = r_nc.get_json()['data']
        self.assertTrue(nc.get('asiento_id'))

        # Consultar asiento de la Nota de Crédito
        r_asnt = self.c.get(f"/api/ventas/accounting/entries?company_id=3101111111&document_id={nc['id']}", headers=self.h_a)
        asientos = r_asnt.get_json()['data']
        asnt_nc = asientos[0]
        self.assertEqual(asnt_nc['tipo_operacion'], 'nota_credito')
        self.assertAlmostEqual(asnt_nc['total_debe'], 56500.0, places=2)
        self.assertAlmostEqual(asnt_nc['total_haber'], 56500.0, places=2)

        # Débito a Devoluciones sobre ventas (4.1.2.01) y Débito a IVA por pagar (2.1.3.01)
        deb_dev = next(l for l in asnt_nc['lineas'] if l['cuenta_codigo'] == '4.1.2.01')
        deb_iva = next(l for l in asnt_nc['lineas'] if l['cuenta_codigo'] == '2.1.3.01')
        cre_caja = next(l for l in asnt_nc['lineas'] if l['cuenta_codigo'] == '1.1.1.01.01')

        self.assertAlmostEqual(deb_dev['debito'], 50000.0, places=2)
        self.assertAlmostEqual(deb_iva['debito'], 6500.0, places=2)
        self.assertAlmostEqual(cre_caja['credito'], 56500.0, places=2)

    # ── 7. Asiento de Abono y Cancelación de CxC ────────────────────
    def test_07_asiento_abono_pago_cxc(self):
        # Emitir factura a crédito por 100 000
        r_fac = self.c.post('/api/ventas/documents', headers=self.h_a, json={
            'company_id': '3101111111',
            'tipo_documento': '01',
            'condicion_venta': '06',
            'lineas': [{
                'descripcion': 'Lote de productos a crédito',
                'codigo_cabys': '1234567890123',
                'cantidad': 1,
                'precio_unitario': 100000.0,
                'tarifa_iva_codigo': '01', # Exento
                'tarifa_iva_porcentaje': 0.0
            }]
        })
        fac = r_fac.get_json()['data']

        # Obtener la CxC creada
        r_cxc = self.c.get('/api/ventas/cxc?company_id=3101111111', headers=self.h_a)
        items = r_cxc.get_json()['data']['items']
        ar_item = next(it for it in items if it['document_id'] == fac['id'])
        ar_id = ar_item['id']

        # Registrar abono parcial de 40 000 por Transferencia Bancaria ('04')
        r_pay = self.c.post(f'/api/ventas/cxc/{ar_id}/payment', headers=self.h_a, json={
            'company_id': '3101111111',
            'monto_abono': 40000.0,
            'medio_pago': '04', # Transferencia / SINPE
            'referencia': 'TRANSF-BANCO-7788'
        })
        self.assertEqual(r_pay.status_code, 200)
        pay_res = r_pay.get_json()
        asiento_pago_id = pay_res['asiento_id']
        self.assertTrue(asiento_pago_id)

        # Comprobar actualización de saldo CxC (saldo pendiente debe ser 60 000, estado 'parcial')
        ar_updated = pay_res['data']
        self.assertEqual(ar_updated['estado'], 'parcial')
        self.assertAlmostEqual(ar_updated['saldo_pendiente'], 60000.0, places=2)

        # Consultar asiento del abono en BD
        with database.get_db(self.tmp_db) as conn:
            asnt_row = conn.execute("SELECT * FROM asientos WHERE id = ?", (asiento_pago_id,)).fetchone()
            self.assertIsNotNone(asnt_row)
            self.assertEqual(asnt_row['tipo_operacion'], 'pago_cxc')
            self.assertAlmostEqual(asnt_row['total_debe'], 40000.0, places=2)
            self.assertAlmostEqual(asnt_row['total_haber'], 40000.0, places=2)

            lineas = conn.execute("SELECT * FROM asiento_lineas WHERE asiento_id = ?", (asiento_pago_id,)).fetchall()
            deb_banco = next(l for l in lineas if l['cuenta_codigo'] == '1.1.1.02.01')
            cre_cxc = next(l for l in lineas if l['cuenta_codigo'] == '1.1.2.01')
            self.assertAlmostEqual(deb_banco['debito'], 40000.0, places=2)
            self.assertAlmostEqual(cre_cxc['credito'], 40000.0, places=2)

    # ── 8. Asientos Automáticos en Punto de Venta (POS) ─────────────
    def test_08_asiento_pos_venta_mostrador_y_movimientos(self):
        # Abrir turno POS
        r_open = self.c.post('/api/ventas/pos/shifts/open', headers=self.h_caj, json={
            'company_id': '3101111111',
            'monto_apertura': 10000.0,
            'codigo_sucursal': '001',
            'codigo_terminal': '00001'
        })
        sid = r_open.get_json()['data']['id']

        # Realizar venta rápida POS cobrada en efectivo
        r_sale = self.c.post('/api/ventas/pos/sales', headers=self.h_caj, json={
            'company_id': '3101111111',
            'shift_id': sid,
            'tipo_documento': '04', # Tiquete
            'medio_pago': '01',     # Efectivo
            'monto_recibido': 5000.0,
            'lineas': [{
                'descripcion': 'Bebida gaseosa',
                'codigo_cabys': '1234567890123',
                'cantidad': 2,
                'precio_unitario': 1000.0,
                'tarifa_iva': 13.0
            }]
        })
        self.assertEqual(r_sale.status_code, 200)
        sale_res = r_sale.get_json()['data']
        self.assertTrue(sale_res.get('asiento_id'))

        # Registrar retiro de efectivo en turno (e.g. pago a mensajero)
        r_mov = self.c.post(f'/api/ventas/pos/shifts/{sid}/movements', headers=self.h_caj, json={
            'company_id': '3101111111',
            'tipo': 'retiro',
            'monto': 3000.0,
            'motivo': 'Pago de flete de mensajería'
        })
        self.assertEqual(r_mov.status_code, 200)

        # Verificar que el movimiento haya generado asiento contable en asientos
        with database.get_db(self.tmp_db) as conn:
            mov = conn.execute("SELECT asiento_id FROM pos_movements WHERE shift_id = ?", (sid,)).fetchone()
            self.assertIsNotNone(mov)
            asnt_mov_id = mov['asiento_id']
            self.assertTrue(asnt_mov_id)

            asnt = conn.execute("SELECT * FROM asientos WHERE id = ?", (asnt_mov_id,)).fetchone()
            self.assertEqual(asnt['tipo_operacion'], 'pos_retiro')
            self.assertAlmostEqual(asnt['total_debe'], 3000.0, places=2)
            self.assertAlmostEqual(asnt['total_haber'], 3000.0, places=2)

    # ── 9. Desactivar Auto-Generación y Generación Manual ───────────
    def test_09_desactivar_autogeneracion_asientos(self):
        # Desactivar auto-generación de asientos
        self.c.put('/api/ventas/accounting/mapping', headers=self.h_a, json={
            'company_id': '3101111111',
            'auto_generar_asientos': 0
        })

        # Emitir factura
        r_emit = self.c.post('/api/ventas/documents', headers=self.h_a, json={
            'company_id': '3101111111',
            'tipo_documento': '01',
            'condicion_venta': '01',
            'lineas': [{
                'descripcion': 'Servicio sin asiento inmediato',
                'codigo_cabys': '1234567890123',
                'cantidad': 1,
                'precio_unitario': 15000.0,
                'tarifa_iva_codigo': '08',
                'tarifa_iva_porcentaje': 13.0
            }]
        })
        doc = r_emit.get_json()['data']
        # No debe haberse generado asiento automático
        self.assertFalse(doc.get('asiento_id'))

        # Ahora generar manualmente el asiento vía endpoint
        r_gen = self.c.post('/api/ventas/accounting/generate', headers=self.h_a, json={
            'company_id': '3101111111',
            'document_id': doc['id']
        })
        self.assertEqual(r_gen.status_code, 200)
        res = r_gen.get_json()['data']
        self.assertTrue(res.get('asiento_id'))

        # Verificar que quedó registrado en el comprobante
        r_entries = self.c.get(f"/api/ventas/accounting/entries?company_id=3101111111&document_id={doc['id']}", headers=self.h_a)
        self.assertEqual(len(r_entries.get_json()['data']), 1)

    # ── 10. Seguridad de Roles y Aislamiento Multiempresa ───────────
    def test_10_seguridad_roles_y_aislamiento_multiempresa_contabilidad(self):
        # Cajero NO tiene permiso para modificar mapeo contable (403)
        r_caj_put = self.c.put('/api/ventas/accounting/mapping', headers=self.h_caj, json={
            'company_id': '3101111111',
            'cuenta_caja_efectivo': '9.9.9.99'
        })
        self.assertEqual(r_caj_put.status_code, 403)

        # Contador SÍ puede ver y modificar el mapeo contable (200)
        r_con_get = self.c.get('/api/ventas/accounting/mapping?company_id=3101111111', headers=self.h_con)
        self.assertEqual(r_con_get.status_code, 200)

        # Dueño de Empresa Beta NO puede ver mapeo de Empresa Alfa (403)
        r_beta_try = self.c.get('/api/ventas/accounting/mapping?company_id=3101111111', headers=self.h_b)
        self.assertEqual(r_beta_try.status_code, 403)

        # Dueño de Beta NO puede ver cuentas por cobrar de Alfa (403)
        r_beta_cxc = self.c.get('/api/ventas/cxc?company_id=3101111111', headers=self.h_b)
        self.assertEqual(r_beta_cxc.status_code, 403)

    # ── 11. Integración en UI HTML (Pantalla CxC, Mapeo NIIF y Modales) ───────────
    def test_11_estructura_ui_html_integracion_contable(self):
        """Verifica que FACTURAMI.html cuente con la interfaz completa de CxC, asientos NIIF y mapeo contable."""
        import os
        html_path = os.path.join(os.path.dirname(__file__), '..', 'FACTURAMI.html')
        self.assertTrue(os.path.exists(html_path), "No se encontró FACTURAMI.html")
        with open(html_path, 'r', encoding='utf-8') as f:
            html = f.read()

        # Página CxC y navegación
        self.assertIn('id="vpg-v-cxc"', html)
        self.assertIn('id="v-cxc-dynamic-content"', html)
        self.assertIn("pageId === 'v-cxc'", html)
        self.assertIn('vLoadCxC()', html)

        # Modales de Abono CxC y Visualizador de Asientos NIIF
        self.assertIn('id="vModalAbonoCxC"', html)
        self.assertIn('id="vAbonoCxCForm"', html)
        self.assertIn('id="v_ar_abono_monto"', html)
        self.assertIn('id="v_ar_abono_mp"', html)
        self.assertIn('id="vModalAsiento"', html)
        self.assertIn('id="v_asiento_modal_body"', html)

        # Configuración contable NIIF en panel de config
        self.assertIn('id="cfg_accounting_card"', html)
        self.assertIn('id="cfg_accounting_body"', html)

        # Funciones JS de Fase 7
        self.assertIn('function vLoadCxC(', html)
        self.assertIn('function vFilterCxC(', html)
        self.assertIn('function vOpenAbonoCxCModal(', html)
        self.assertIn('function vSaveAbonoCxC(', html)
        self.assertIn('function vViewDocumentAccounting(', html)
        self.assertIn('function vGenerateAccountingEntryForDoc(', html)
        self.assertIn('function vLoadAccountingConfig(', html)
        self.assertIn('function vSaveAccountingConfig(', html)

        # Botón de consulta de Asiento NIIF en comprobantes emitidos
        self.assertIn("vViewDocumentAccounting('${doc.id}')", html)
        self.assertIn("vViewDocumentAccounting('${d.id}')", html)


if __name__ == '__main__':
    unittest.main()
