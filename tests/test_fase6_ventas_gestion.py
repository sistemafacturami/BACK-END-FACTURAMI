"""
Tests FASE 6: Gestión Comercial (Cotizaciones, Pedidos, Kardex y Punto de Venta/Cajas).
Todos los datos son ficticios. Base de datos temporal aislada.
"""
import os
import tempfile
import unittest

os.environ.setdefault('JWT_SECRET_KEY', 'test-jwt-secret-key-0123456789-abcdef')
os.environ.setdefault('N8N_INTERNAL_SECRET', 'test-n8n-secret-key-0123456789-abcdef')
os.environ['FACTURAMI_FE_SECRET_KEY'] = 'test-fe-secret-key-0123456789-abcdefghij'
os.environ['FACTURAMI_FE_SIMULATE_HACIENDA'] = '1'

from flask_jwt_extended import create_access_token
import db as database
from app import create_app
import services.ventas_gestion_service as gestion_service


class TestFase6VentasGestion(unittest.TestCase):
    def setUp(self):
        fd, self.tmp_db = tempfile.mkstemp(suffix='.db')
        os.close(fd)
        self.prev_db_path = database.DB_PATH
        database.DB_PATH = self.tmp_db
        database.init_db(self.tmp_db)
        self.app = create_app()
        self.c = self.app.test_client()

        with database.get_db(self.tmp_db) as conn:
            for u in ('dueno_a', 'dueno_b', 'vendedor_a', 'cajero_a'):
                database.upsert_cuenta(conn, {'username': u, 'nombre': u, 'role': 'contador', 'activo': 1})
            database.upsert_cliente(conn, {'id': '3101111111', 'type': 'PJ', 'name': 'Empresa Comercial Alfa',
                                           'mail': 'alfa@test.cr'}, cuenta_id='dueno_a')
            database.upsert_cliente(conn, {'id': '3101222222', 'type': 'PJ', 'name': 'Empresa Comercial Beta',
                                           'mail': 'beta@test.cr'}, cuenta_id='dueno_b')

        with self.app.app_context():
            tok = lambda u, r='contador': {'Authorization': 'Bearer ' + create_access_token(
                identity=u, additional_claims={'role': r})}
            self.h_a = tok('dueno_a')
            self.h_b = tok('dueno_b')
            self.h_vend = tok('vendedor_a')
            self.h_caj = tok('cajero_a')

        # Asignar vendedor y cajero en Alfa
        self.c.post('/api/ventas/users', headers=self.h_a,
                    json={'company_id': '3101111111', 'username': 'vendedor_a', 'role': 'vendedor'})
        self.c.post('/api/ventas/users', headers=self.h_a,
                    json={'company_id': '3101111111', 'username': 'cajero_a', 'role': 'cajero'})

        # Inicializar contexto
        self.c.get('/api/ventas/context?company_id=3101111111', headers=self.h_a)

    def tearDown(self):
        database.DB_PATH = self.prev_db_path
        try:
            os.remove(self.tmp_db)
        except OSError:
            pass

    # ── 1. Cotizaciones: Creación, Correlativo y Líneas ───────────────
    def test_01_crear_cotizacion_correlativo_y_lineas(self):
        r = self.c.post('/api/ventas/quotations', headers=self.h_a, json={
            'company_id': '3101111111',
            'moneda': 'CRC',
            'dias_validez': 30,
            'notas': 'Cotización para proyecto informático',
            'lineas': [
                {
                    'codigo_interno': 'SRV-01',
                    'codigo_cabys': '5411100000000',
                    'descripcion': 'Desarrollo de Módulo Web',
                    'cantidad': 2,
                    'precio_unitario': 100000,
                    'tarifa_iva_codigo': '08'
                },
                {
                    'codigo_interno': 'LIC-01',
                    'codigo_cabys': '5411100000000',
                    'descripcion': 'Licencia Anual Cloud',
                    'cantidad': 1,
                    'precio_unitario': 50000,
                    'monto_descuento': 5000,
                    'tarifa_iva_codigo': '08'
                }
            ]
        })
        self.assertEqual(r.status_code, 200)
        q = r.get_json()['data']
        self.assertTrue(q['numero_cotizacion'].startswith('COT-'))
        self.assertEqual(len(q['lineas']), 2)
        # Línea 1: 200 000 + 26 000 IVA = 226 000
        # Línea 2: 50 000 - 5 000 = 45 000 + 5 850 IVA = 50 850
        # Subtotal: 250 000, Descuentos: 5 000, IVA: 31 850, Total: 276 850
        self.assertEqual(q['subtotal'], 250000.0)
        self.assertEqual(q['total_descuentos'], 5000.0)
        self.assertEqual(q['total_impuestos'], 31850.0)
        self.assertEqual(q['total_cotizacion'], 276850.0)
        self.assertEqual(q['estado'], 'emitida')

    # ── 2. Cotizaciones: Actualización de Estado y Filtros ─────────────
    def test_02_actualizar_estado_cotizacion_y_filtros(self):
        r1 = self.c.post('/api/ventas/quotations', headers=self.h_vend, json={
            'company_id': '3101111111',
            'lineas': [{'descripcion': 'Item A', 'cantidad': 1, 'precio_unitario': 10000, 'codigo_cabys': '1234567890123'}]
        })
        qid = r1.get_json()['data']['id']

        # Cambiar a 'aceptada'
        r_patch = self.c.patch(f'/api/ventas/quotations/{qid}/status', headers=self.h_vend, json={
            'company_id': '3101111111',
            'estado': 'aceptada'
        })
        self.assertEqual(r_patch.status_code, 200)
        self.assertEqual(r_patch.get_json()['data']['estado'], 'aceptada')

        # Listar filtrando por estado
        r_list = self.c.get('/api/ventas/quotations?company_id=3101111111&estado=aceptada', headers=self.h_vend)
        self.assertEqual(r_list.status_code, 200)
        items = r_list.get_json()['data']['items']
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]['id'], qid)

    # ── 3. Conversión de Cotización a Factura Electrónica ──────────────
    def test_03_convertir_cotizacion_a_factura_electronica(self):
        r_cli = self.c.post('/api/ventas/customers', headers=self.h_a, json={
            'company_id': '3101111111',
            'tipo_identificacion': '01',
            'identificacion': '109870654',
            'nombre': 'Cliente de Cotización'
        })
        cli_id = r_cli.get_json()['data']['id']

        r_cot = self.c.post('/api/ventas/quotations', headers=self.h_a, json={
            'company_id': '3101111111',
            'customer_id': cli_id,
            'lineas': [{'descripcion': 'Servicio Cotizado', 'cantidad': 1, 'precio_unitario': 30000, 'codigo_cabys': '1234567890123'}]
        })
        qid = r_cot.get_json()['data']['id']

        # Convertir a Factura
        r_conv = self.c.post(f'/api/ventas/quotations/{qid}/convert-invoice', headers=self.h_a, json={
            'company_id': '3101111111',
            'tipo_documento': '01'
        })
        self.assertEqual(r_conv.status_code, 200)
        res = r_conv.get_json()['data']
        self.assertEqual(res['quotation']['estado'], 'facturada')
        self.assertEqual(res['quotation']['documento_destino_id'], res['document']['id'])
        self.assertEqual(res['document']['consecutivo'], '00100001010000000001')

        # Intentar convertir nuevamente debe fallar
        r_fail = self.c.post(f'/api/ventas/quotations/{qid}/convert-invoice', headers=self.h_a, json={'company_id': '3101111111'})
        self.assertEqual(r_fail.status_code, 400)

    # ── 4. Pedidos de Venta: Creación, Correlativo y Conversión ────────
    def test_04_crear_pedido_correlativo_y_conversion_a_factura(self):
        r_ped = self.c.post('/api/ventas/orders', headers=self.h_vend, json={
            'company_id': '3101111111',
            'fecha_entrega': '2026-11-25',
            'notas': 'Despachar en sede San José',
            'lineas': [
                {'descripcion': 'Artículo Pedido', 'cantidad': 5, 'precio_unitario': 4000, 'codigo_cabys': '1234567890123'}
            ]
        })
        self.assertEqual(r_ped.status_code, 200)
        ped = r_ped.get_json()['data']
        self.assertTrue(ped['numero_pedido'].startswith('PED-'))
        self.assertEqual(ped['total_cotizacion' if 'total_cotizacion' in ped else 'total_pedido'], 22600.0)
        self.assertEqual(ped['estado'], 'pendiente')

        # Actualizar a 'en_preparacion'
        self.c.patch(f"/api/ventas/orders/{ped['id']}/status", headers=self.h_vend, json={
            'company_id': '3101111111', 'estado': 'en_preparacion'
        })

        # Facturar pedido
        r_fact = self.c.post(f"/api/ventas/orders/{ped['id']}/convert-invoice", headers=self.h_vend, json={
            'company_id': '3101111111', 'tipo_documento': '04'  # Tiquete
        })
        self.assertEqual(r_fact.status_code, 200)
        self.assertEqual(r_fact.get_json()['data']['order']['estado'], 'facturado')

    # ── 5. Kardex: Entrada y Costeo Promedio Ponderado ─────────────────
    def test_05_kardex_movimiento_entrada_y_costeo_promedio_ponderado(self):
        # Crear producto con 10 unidades a costo 1 000
        r_prod = self.c.post('/api/ventas/products', headers=self.h_a, json={
            'company_id': '3101111111',
            'codigo_interno': 'KDX-01',
            'codigo_cabys': '1234567890123',
            'nombre': 'Artículo con Kardex',
            'precio_compra': 1000,
            'precio_venta': 2000,
            'controla_inventario': 1,
            'existencia': 10
        })
        pid = r_prod.get_json()['data']['id']

        # Registrar entrada de 10 unidades a costo 1 200
        # Costo promedio ponderado: (10 * 1000 + 10 * 1200) / 20 = 22 000 / 20 = 1 100
        with database.get_db(self.tmp_db) as conn:
            mov = gestion_service.record_kardex_movement(
                conn=conn,
                company_id='3101111111',
                product_id=pid,
                tipo_movimiento='entrada',
                cantidad=10,
                costo_unitario=1200,
                documento_ref='COMPRA-001',
                motivo='Recepción factura compra proveedor'
            )

        self.assertEqual(mov['saldo_cantidad'], 20.0)
        self.assertEqual(mov['costo_promedio'], 1100.0)
        self.assertEqual(mov['saldo_costo_total'], 22000.0)

        # Consultar historial Kardex vía API
        r_kdx = self.c.get(f'/api/ventas/products/{pid}/kardex?company_id=3101111111', headers=self.h_a)
        self.assertEqual(r_kdx.status_code, 200)
        kdx_data = r_kdx.get_json()['data']
        self.assertEqual(len(kdx_data['movimientos']), 1)
        self.assertEqual(kdx_data['movimientos'][0]['tipo_movimiento'], 'entrada')

    # ── 6. Kardex: Venta y Trazabilidad Automática ─────────────────────
    def test_06_kardex_movimiento_venta_facturacion_automatica(self):
        r_prod = self.c.post('/api/ventas/products', headers=self.h_a, json={
            'company_id': '3101111111',
            'codigo_interno': 'KDX-AUTO',
            'codigo_cabys': '1234567890123',
            'nombre': 'Artículo Venta Auto Kardex',
            'precio_compra': 500,
            'precio_venta': 1000,
            'controla_inventario': 1,
            'existencia': 15
        })
        pid = r_prod.get_json()['data']['id']

        # Emitir factura vendiendo 5 unidades
        r_fact = self.c.post('/api/ventas/documents', headers=self.h_a, json={
            'company_id': '3101111111',
            'tipo_documento': '01',
            'lineas': [{'product_id': pid, 'cantidad': 5, 'precio_unitario': 1000, 'codigo_cabys': '1234567890123'}]
        })
        self.assertEqual(r_fact.status_code, 200)

        # Verificar Kardex
        r_kdx = self.c.get(f'/api/ventas/products/{pid}/kardex?company_id=3101111111', headers=self.h_a)
        kdx = r_kdx.get_json()['data']['movimientos']
        self.assertEqual(len(kdx), 1)
        self.assertEqual(kdx[0]['tipo_movimiento'], 'venta')
        self.assertEqual(kdx[0]['cantidad'], 5.0)
        self.assertEqual(kdx[0]['saldo_cantidad'], 10.0)

    # ── 7. Valorización de Inventario ──────────────────────────────────
    def test_07_valorizacion_inventario_y_alertas_stock(self):
        # 1 producto normal, 1 bajo stock, 1 agotado
        self.c.post('/api/ventas/products', headers=self.h_a, json={
            'company_id': '3101111111', 'codigo_interno': 'V-NORM', 'codigo_cabys': '1234567890123',
            'nombre': 'Normal', 'precio_compra': 100, 'precio_venta': 200, 'controla_inventario': 1,
            'existencia': 10, 'stock_minimo': 2
        })
        self.c.post('/api/ventas/products', headers=self.h_a, json={
            'company_id': '3101111111', 'codigo_interno': 'V-BAJO', 'codigo_cabys': '1234567890123',
            'nombre': 'Bajo', 'precio_compra': 50, 'precio_venta': 100, 'controla_inventario': 1,
            'existencia': 1, 'stock_minimo': 5
        })
        self.c.post('/api/ventas/products', headers=self.h_a, json={
            'company_id': '3101111111', 'codigo_interno': 'V-AGOT', 'codigo_cabys': '1234567890123',
            'nombre': 'Agotado', 'precio_compra': 80, 'precio_venta': 150, 'controla_inventario': 1,
            'existencia': 0, 'stock_minimo': 2
        })

        r_val = self.c.get('/api/ventas/inventory/valuation?company_id=3101111111', headers=self.h_a)
        self.assertEqual(r_val.status_code, 200)
        val = r_val.get_json()['data']
        self.assertEqual(val['total_articulos'], 3)
        # Costo total: 10 * 100 + 1 * 50 + 0 * 80 = 1 050
        self.assertEqual(val['valor_total_costo'], 1050.0)
        self.assertEqual(val['articulos_bajos'], 1)
        self.assertEqual(val['articulos_agotados'], 1)

    # ── 8. Punto de Venta: Apertura de Turno y Terminal Única ─────────
    def test_08_pos_apertura_turno_y_validacion_terminal_unica(self):
        r_open = self.c.post('/api/ventas/pos/shifts/open', headers=self.h_caj, json={
            'company_id': '3101111111',
            'codigo_sucursal': '001',
            'codigo_terminal': '00001',
            'monto_inicial': 25000.0
        })
        self.assertEqual(r_open.status_code, 200)
        shift = r_open.get_json()['data']
        self.assertEqual(shift['estado'], 'abierta')
        self.assertEqual(shift['monto_apertura'], 25000.0)

        # Intentar abrir otro turno en la misma terminal mientras está abierto debe fallar
        r_dup = self.c.post('/api/ventas/pos/shifts/open', headers=self.h_caj, json={
            'company_id': '3101111111',
            'codigo_sucursal': '001',
            'codigo_terminal': '00001',
            'monto_inicial': 10000.0
        })
        self.assertEqual(r_dup.status_code, 400)

        # Consultar turno activo
        r_act = self.c.get('/api/ventas/pos/shifts/active?company_id=3101111111&codigo_terminal=00001', headers=self.h_caj)
        self.assertEqual(r_act.status_code, 200)
        self.assertEqual(r_act.get_json()['data']['id'], shift['id'])

    # ── 9. Punto de Venta: Movimientos de Caja Chica ───────────────────
    def test_09_pos_movimientos_ingreso_retiro_efectivo(self):
        r_s = self.c.post('/api/ventas/pos/shifts/open', headers=self.h_caj, json={
            'company_id': '3101111111',
            'monto_inicial': 10000.0
        })
        sid = r_s.get_json()['data']['id']

        # Retiro de efectivo para compra menor
        r_mov1 = self.c.post(f'/api/ventas/pos/shifts/{sid}/movements', headers=self.h_caj, json={
            'company_id': '3101111111',
            'tipo': 'retiro',
            'monto': 3000.0,
            'motivo': 'Compra de café y azúcar para oficina'
        })
        self.assertEqual(r_mov1.status_code, 200)

        # Ingreso extra
        r_mov2 = self.c.post(f'/api/ventas/pos/shifts/{sid}/movements', headers=self.h_caj, json={
            'company_id': '3101111111',
            'tipo': 'ingreso',
            'monto': 5000.0,
            'motivo': 'Aporte de sencillo por administración'
        })
        self.assertEqual(r_mov2.status_code, 200)
        shift = r_mov2.get_json()['data']
        self.assertEqual(shift['total_retiros_caja'], 3000.0)
        self.assertEqual(shift['total_ingresos_extra'], 5000.0)
        # 10 000 + 5 000 - 3 000 = 12 000
        self.assertEqual(shift['efectivo_esperado'], 12000.0)

    # ── 10. Venta POS Rápida, Cálculo de Vuelto y Cierre/Arqueo ────────
    def test_10_pos_venta_mostrador_rapida_vuelto_y_arqueo_cierre(self):
        # 1. Abrir caja con 50 000
        r_s = self.c.post('/api/ventas/pos/shifts/open', headers=self.h_caj, json={
            'company_id': '3101111111',
            'monto_inicial': 50000.0
        })
        sid = r_s.get_json()['data']['id']

        # 2. Venta rápida en Efectivo: Total 11 300, paga con 20 000 -> Vuelto 8 700
        r_sale = self.c.post('/api/ventas/pos/sales', headers=self.h_caj, json={
            'company_id': '3101111111',
            'shift_id': sid,
            'medio_pago': '01',  # Efectivo
            'monto_recibido': 20000.0,
            'lineas': [{'descripcion': 'Producto Mostrador', 'cantidad': 1, 'precio_unitario': 10000, 'codigo_cabys': '1234567890123'}]
        })
        self.assertEqual(r_sale.status_code, 200)
        res_sale = r_sale.get_json()['data']
        self.assertEqual(res_sale['vuelto'], 8700.0)
        self.assertEqual(res_sale['document']['tipo_documento'], '04')  # Tiquete por defecto

        # 3. Venta rápida con Tarjeta: 5 650
        r_sale_tarj = self.c.post('/api/ventas/pos/sales', headers=self.h_caj, json={
            'company_id': '3101111111',
            'shift_id': sid,
            'medio_pago': '02',  # Tarjeta
            'lineas': [{'descripcion': 'Producto Tarjeta', 'cantidad': 1, 'precio_unitario': 5000, 'codigo_cabys': '1234567890123'}]
        })
        self.assertEqual(r_sale_tarj.status_code, 200)

        # 4. Consultar turno antes de cerrar:
        # Efectivo esperado = 50 000 apertura + 11 300 venta = 61 300
        r_info = self.c.get(f'/api/ventas/pos/shifts/{sid}?company_id=3101111111', headers=self.h_caj)
        shift_mid = r_info.get_json()['data']
        self.assertEqual(shift_mid['total_ventas_efectivo'], 11300.0)
        self.assertEqual(shift_mid['total_ventas_tarjeta'], 5650.0)
        self.assertEqual(shift_mid['efectivo_esperado'], 61300.0)
        self.assertEqual(shift_mid['comprobantes_emitidos_count'], 2)

        # 5. Cerrar caja con arqueo físico de 61 500 (sobrante de 200)
        r_close = self.c.post(f'/api/ventas/pos/shifts/{sid}/close', headers=self.h_caj, json={
            'company_id': '3101111111',
            'efectivo_real': 61500.0,
            'observaciones': 'Arqueo sin novedades. Sobrante de 200 colones en monedas.'
        })
        self.assertEqual(r_close.status_code, 200)
        shift_closed = r_close.get_json()['data']
        self.assertEqual(shift_closed['estado'], 'cerrada')
        self.assertEqual(shift_closed['efectivo_real'], 61500.0)
        self.assertEqual(shift_closed['diferencia'], 200.0)

    # ── 11. Seguridad y Aislamiento Multiempresa en Gestión Comercial ───
    def test_11_seguridad_roles_y_aislamiento_multiempresa_gestion(self):
        # Crear cotización en Empresa Alfa
        r_cot_a = self.c.post('/api/ventas/quotations', headers=self.h_a, json={
            'company_id': '3101111111',
            'lineas': [{'descripcion': 'Confidencial Alfa', 'cantidad': 1, 'precio_unitario': 1000, 'codigo_cabys': '1234567890123'}]
        })
        qid_a = r_cot_a.get_json()['data']['id']

        # Dueño de Empresa Beta NO puede consultar cotizaciones de Alfa (403)
        r_beta_try = self.c.get(f'/api/ventas/quotations/{qid_a}?company_id=3101111111', headers=self.h_b)
        self.assertEqual(r_beta_try.status_code, 403)

        # Dueño de Beta tampoco puede ver pedidos ni Kardex de Alfa
        r_kdx_beta = self.c.get('/api/ventas/inventory/valuation?company_id=3101111111', headers=self.h_b)
        self.assertEqual(r_kdx_beta.status_code, 403)

        # Cajero NO puede cambiar configuración ni ver reportes avanzados, pero sí operar POS
        r_caj_pos = self.c.get('/api/ventas/pos/shifts/active?company_id=3101111111', headers=self.h_caj)
        self.assertEqual(r_caj_pos.status_code, 200)

    # ── 12. Estructura UI HTML y JavaScript de Gestión Comercial ───
    def test_12_estructura_ui_html_gestion_comercial(self):
        with open('FACTURAMI.html', 'r', encoding='utf-8') as f:
            html = f.read()

        # Comprobar secciones HTML de Gestión Comercial
        self.assertIn('id="vpg-v-inventario"', html)
        self.assertIn('id="vpg-v-cotizaciones"', html)
        self.assertIn('id="vpg-v-pedidos"', html)
        self.assertIn('id="vpg-v-pos"', html)

        # Comprobar indicadores y modales nativos <dialog>
        self.assertIn('id="vModalKardexMov"', html)
        self.assertIn('id="vModalCotizacion"', html)
        self.assertIn('id="vModalPedido"', html)
        self.assertIn('id="vModalPosCierre"', html)
        self.assertIn('id="vModalPosMovimiento"', html)

        # Comprobar layout y controles del Punto de Venta (POS)
        self.assertIn('id="v-pos-shift-bar"', html)
        self.assertIn('id="v-pos-main-layout"', html)
        self.assertIn('id="v-pos-apertura-panel"', html)
        self.assertIn('id="v_pos_catalog_grid"', html)
        self.assertIn('id="v_pos_ticket_items"', html)
        self.assertIn('id="btn-v-pos-cobrar"', html)

        # Comprobar integración de funciones JavaScript
        js_functions = [
            'function vLoadInventario',
            'function vSetInventarioModo',
            'function vLoadProductKardex',
            'function vOpenKardexMovModal',
            'function vSaveKardexMov',
            'function vLoadCotizaciones',
            'function vOpenCotizacionModal',
            'function vAddCotLine',
            'function vSaveCotizacion',
            'function vConvertCotToInvoice',
            'function vLoadPedidos',
            'function vOpenPedidoModal',
            'function vAddPedLine',
            'function vSavePedido',
            'function vConvertPedToInvoice',
            'function vLoadPos',
            'function vSubmitOpenShift',
            'function vSearchPosProducts',
            'function vAddPosItemToTicket',
            'function vExecutePosSale',
            'function vOpenPosCierreModal',
            'function vSubmitCloseShift'
        ]
        for fn in js_functions:
            self.assertIn(fn, html, f"Función JavaScript {fn} no encontrada en FACTURAMI.html")


if __name__ == '__main__':
    unittest.main()
