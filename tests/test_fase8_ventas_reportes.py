"""
FACTURAMI - Pruebas Unitarias y de Integración: FASE 8 (Reportes y Estadísticas Comerciales y Fiscales).
========================================================================================================
Valida:
  1. Reporte Fiscal D-104 / Resumen de IVA (desglose por tarifas 13%, 8%, 4%, 2%, 1%, 0% y notas de crédito).
  2. Reporte Comercial de Ventas por Período y filtros por cliente, medio de pago y condición.
  3. Reporte de Utilidad Bruta y Margen Comercial por producto/línea.
  4. Reporte de Antigüedad de Saldos de Cuentas por Cobrar (0-30, 31-60, 61-90, +90 días).
  5. Reporte de Auditoría de Cierres de Turno de Caja POS (arqueos y diferencias).
  6. Exportación a CSV y PDF de cada reporte.
  7. Seguridad de roles y aislamiento multiempresa estricto.
"""
import os
import unittest
import tempfile
import sqlite3
from datetime import datetime, date, timedelta
from flask_jwt_extended import create_access_token

from app import create_app
import db as database
import services.ventas_service as ventas_service
import services.ventas_comercial_service as comercial_service
import services.fe_emission_service as emission_service
import services.ventas_gestion_service as gestion_service
import services.ventas_reportes_service as reportes_service


class TestFase8VentasReportes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.orig_db = database.DB_PATH

    @classmethod
    def tearDownClass(cls):
        database.DB_PATH = cls.orig_db

    def setUp(self):
        fd, self.tmp_db = tempfile.mkstemp(suffix='.db')
        os.close(fd)
        database.DB_PATH = self.tmp_db
        database.init_db(self.tmp_db)
        ventas_service.init_ventas_schema(self.tmp_db)

        self.app = create_app()
        self.app.config['TESTING'] = True
        self.c = self.app.test_client()

        with database.get_db(self.tmp_db) as conn:
            # Crear cuentas activas
            for u, r in [('admin_alfa', 'admin'), ('contador_alfa', 'contador'),
                         ('vendedor_alfa', 'vendedor'), ('cajero_alfa', 'cajero'),
                         ('dueno_beta', 'admin')]:
                database.upsert_cuenta(conn, {'username': u, 'nombre': u, 'role': r, 'activo': 1})

            # Empresa Alfa (3-101-111111) y Empresa Beta (3-102-222222)
            database.upsert_cliente(conn, {'id': '3101111111', 'type': 'PJ', 'name': 'Empresa Alfa S.A.', 'mail': 'alfa@empresa.cr'}, cuenta_id='admin_alfa')
            database.upsert_cliente(conn, {'id': '3102222222', 'type': 'PJ', 'name': 'Empresa Beta S.A.', 'mail': 'beta@empresa.cr'}, cuenta_id='dueno_beta')

        with self.app.app_context():
            tok = lambda u, r='admin': {'Authorization': 'Bearer ' + create_access_token(
                identity=u, additional_claims={'role': r})}
            self.h_a = tok('admin_alfa')
            self.h_b = tok('dueno_beta')
            self.h_con = tok('contador_alfa', 'contador')
            self.h_ven = tok('vendedor_alfa', 'vendedor')
            self.h_caj = tok('cajero_alfa', 'cajero')

        # Asignar roles internos en Alfa
        self.c.post('/api/ventas/users', headers=self.h_a,
                    json={'company_id': '3101111111', 'username': 'contador_alfa', 'role': 'contador'})
        self.c.post('/api/ventas/users', headers=self.h_a,
                    json={'company_id': '3101111111', 'username': 'vendedor_alfa', 'role': 'vendedor'})
        self.c.post('/api/ventas/users', headers=self.h_a,
                    json={'company_id': '3101111111', 'username': 'cajero_alfa', 'role': 'cajero'})

        # Inicializar contexto de ambas empresas
        self.c.get('/api/ventas/context?company_id=3101111111', headers=self.h_a)
        self.c.get('/api/ventas/context?company_id=3102222222', headers=self.h_b)

    def tearDown(self):
        database.DB_PATH = self.orig_db
        try:
            os.unlink(self.tmp_db)
        except Exception:
            pass

    # ── 1. Reporte Fiscal D-104 / Resumen de IVA Mensual ──────────────
    def test_01_reporte_fiscal_d104_resumen_iva(self):
        # Crear cliente y productos
        cli = comercial_service.create_customer('3101111111', {
            'tipo_identificacion': '01',
            'identificacion': '109990888',
            'nombre': 'Cliente Tributario'
        }, 'admin_alfa')
        p13 = comercial_service.create_product('3101111111', {
            'codigo_interno': 'G13',
            'nombre': 'Producto Gravado 13%',
            'codigo_cabys': '1234567890123',
            'tarifa_iva_codigo': '08',
            'tarifa_iva_porcentaje': 13.0,
            'precio_venta': 10000.0,
            'precio_compra': 6000.0
        }, 'admin_alfa')
        p4 = comercial_service.create_product('3101111111', {
            'codigo_interno': 'R04',
            'nombre': 'Producto Reducido 4%',
            'codigo_cabys': '1234567890124',
            'tarifa_iva_codigo': '04',
            'tarifa_iva_porcentaje': 4.0,
            'precio_venta': 5000.0,
            'precio_compra': 3000.0
        }, 'admin_alfa')

        # Emitir Factura 1: 1 unidad de p13 (10,000 + 1,300 IVA) y 2 de p4 (10,000 + 400 IVA)
        # Total Factura: Subtotal 20,000 + IVA 1,700 = 21,700
        fe1 = emission_service.emitir_comprobante('3101111111', {
            'tipo_documento': '01',
            'customer_id': cli['id'],
            'condicion_venta': '01',
            'medio_pago': '01',
            'lineas': [
                {'product_id': p13['id'], 'cantidad': 1, 'precio_unitario': 10000.0, 'tarifa_iva_codigo': '08', 'tarifa_iva_porcentaje': 13.0},
                {'product_id': p4['id'], 'cantidad': 2, 'precio_unitario': 5000.0, 'tarifa_iva_codigo': '04', 'tarifa_iva_porcentaje': 4.0}
            ]
        }, 'admin_alfa')

        # Emitir Nota de Crédito devolviendo la unidad de p13 (10,000 + 1,300 IVA)
        nc1 = emission_service.emitir_comprobante('3101111111', {
            'tipo_documento': '03',
            'customer_id': cli['id'],
            'referencia': {
                'tipo_documento': '01',
                'numero': fe1['consecutivo'],
                'fecha_emision': fe1['fecha_emision'],
                'codigo': '01',
                'razon': 'Devolución parcial p13'
            },
            'lineas': [
                {'product_id': p13['id'], 'cantidad': 1, 'precio_unitario': 10000.0, 'tarifa_iva': 13.0}
            ]
        }, 'admin_alfa')

        # Consultar reporte D-104 vía API
        res = self.c.get('/api/ventas/reports/d104?company_id=3101111111', headers=self.h_con)
        self.assertEqual(res.status_code, 200)
        d = res.get_json()['data']

        # Verificaciones fiscales:
        # Ventas brutas: 20,000
        # Devoluciones NC: 10,000
        # Ventas netas: 10,000
        # IVA bruto: 1,700
        # IVA NC: 1,300
        # IVA neto a declarar: 400
        resumen = d['resumen']
        self.assertEqual(resumen['ventas_brutas'], 20000.0)
        self.assertEqual(resumen['devoluciones_nc'], 10000.0)
        self.assertEqual(resumen['ventas_netas'], 10000.0)
        self.assertEqual(resumen['iva_debito_bruto'], 1700.0)
        self.assertEqual(resumen['iva_nc_reversion'], 1300.0)
        self.assertEqual(resumen['iva_debito_neto_declarar'], 400.0)

        # Desglose de tarifas: 13% base neta = 0, IVA neto = 0; 4% base neta = 10,000, IVA neto = 400
        desglose = {t['tarifa_porcentaje']: t for t in d['desglose_por_tarifa']}
        self.assertIn(4.0, desglose)
        self.assertEqual(desglose[4.0]['base_imponible'], 10000.0)
        self.assertEqual(desglose[4.0]['monto_iva'], 400.0)

    # ── 2. Reporte Comercial de Ventas por Período ───────────────────
    def test_02_reporte_ventas_periodo_y_filtros(self):
        cli1 = comercial_service.create_customer('3101111111', {'tipo_identificacion': '01', 'identificacion': '111111111', 'nombre': 'Cliente Contado'}, 'admin_alfa')
        cli2 = comercial_service.create_customer('3101111111', {'tipo_identificacion': '01', 'identificacion': '222222222', 'nombre': 'Cliente Tarjeta'}, 'admin_alfa')

        p = comercial_service.create_product('3101111111', {'codigo_interno': 'ART1', 'nombre': 'Articulo', 'codigo_cabys': '1234567890123', 'precio_venta': 5000.0}, 'admin_alfa')

        # Factura 1: Efectivo 5,000 + 650 = 5,650
        emission_service.emitir_comprobante('3101111111', {
            'tipo_documento': '01', 'customer_id': cli1['id'], 'condicion_venta': '01', 'medio_pago': '01',
            'lineas': [{'product_id': p['id'], 'cantidad': 1, 'precio_unitario': 5000.0, 'tarifa_iva': 13.0}]
        }, 'admin_alfa')
        # Factura 2: Tarjeta 10,000 + 1,300 = 11,300
        emission_service.emitir_comprobante('3101111111', {
            'tipo_documento': '01', 'customer_id': cli2['id'], 'condicion_venta': '01', 'medio_pago': '02',
            'lineas': [{'product_id': p['id'], 'cantidad': 2, 'precio_unitario': 5000.0, 'tarifa_iva': 13.0}]
        }, 'admin_alfa')

        # Reporte general
        res = self.c.get('/api/ventas/reports/sales?company_id=3101111111', headers=self.h_a)
        self.assertEqual(res.status_code, 200)
        data = res.get_json()['data']
        self.assertEqual(data['total_registros'], 2)
        self.assertEqual(data['totales']['subtotal'], 15000.0)
        self.assertEqual(data['totales']['iva'], 1950.0)
        self.assertEqual(data['totales']['total'], 16950.0)
        self.assertEqual(data['por_medio_pago']['efectivo'], 5650.0)
        self.assertEqual(data['por_medio_pago']['tarjeta'], 11300.0)

        # Filtro por medio de pago (solo tarjeta)
        res_mp = self.c.get('/api/ventas/reports/sales?company_id=3101111111&medio_pago=02', headers=self.h_a)
        self.assertEqual(res_mp.status_code, 200)
        self.assertEqual(res_mp.get_json()['data']['total_registros'], 1)

    # ── 3. Reporte de Utilidad Bruta y Margen por Producto ───────────
    def test_03_reporte_utilidad_bruta_y_margen_por_producto(self):
        p1 = comercial_service.create_product('3101111111', {
            'codigo_interno': 'ALTO_MARGEN', 'nombre': 'Servicio Consultoria',
            'codigo_cabys': '1234567890123',
            'precio_venta': 20000.0, 'precio_compra': 5000.0
        }, 'admin_alfa')
        p2 = comercial_service.create_product('3101111111', {
            'codigo_interno': 'BAJO_MARGEN', 'nombre': 'Articulo Reventa',
            'codigo_cabys': '1234567890124',
            'precio_venta': 10000.0, 'precio_compra': 8000.0
        }, 'admin_alfa')

        # Factura: 1 de p1 y 2 de p2
        # p1: Venta 20,000, Costo 5,000, Utilidad 15,000 (75%)
        # p2: Venta 20,000, Costo 16,000, Utilidad 4,000 (20%)
        # Gran Total: Venta 40,000, Costo 21,000, Utilidad 19,000 (47.5%)
        emission_service.emitir_comprobante('3101111111', {
            'tipo_documento': '01', 'condicion_venta': '01', 'medio_pago': '01',
            'lineas': [
                {'product_id': p1['id'], 'codigo_interno': 'ALTO_MARGEN', 'descripcion': 'Servicio Consultoria', 'cantidad': 1, 'precio_unitario': 20000.0, 'tarifa_iva': 13.0},
                {'product_id': p2['id'], 'codigo_interno': 'BAJO_MARGEN', 'descripcion': 'Articulo Reventa', 'cantidad': 2, 'precio_unitario': 10000.0, 'tarifa_iva': 13.0}
            ]
        }, 'admin_alfa')

        res = self.c.get('/api/ventas/reports/profitability?company_id=3101111111', headers=self.h_a)
        self.assertEqual(res.status_code, 200)
        data = res.get_json()['data']
        rg = data['resumen_general']
        self.assertEqual(rg['venta_neta_total'], 40000.0)
        self.assertEqual(rg['costo_ventas_total'], 21000.0)
        self.assertEqual(rg['utilidad_bruta_total'], 19000.0)
        self.assertEqual(rg['margen_bruto_promedio_pct'], 47.5)

        prod_map = {p['codigo']: p for p in data['productos']}
        self.assertEqual(prod_map['ALTO_MARGEN']['utilidad_bruta'], 15000.0)
        self.assertEqual(prod_map['ALTO_MARGEN']['margen_porcentaje'], 75.0)
        self.assertEqual(prod_map['BAJO_MARGEN']['utilidad_bruta'], 4000.0)
        self.assertEqual(prod_map['BAJO_MARGEN']['margen_porcentaje'], 20.0)

    # ── 4. Reporte de Antigüedad de Saldos CxC ───────────────────────
    def test_04_reporte_antiguedad_saldos_cxc(self):
        cli = comercial_service.create_customer('3101111111', {'nombre': 'Cliente Deudor', 'identificacion': '300010002'}, 'admin_alfa')
        today = date.today()

        with database.get_db(self.tmp_db) as conn:
            # Documentos de referencia para consecutivo
            for i, num in [(1, '00100001010000000001'), (2, '00100001010000000002'), (3, '00100001010000000003')]:
                conn.execute("""
                    INSERT INTO electronic_documents (id, company_id, consecutivo, fecha_emision, tipo_documento, total_comprobante)
                    VALUES (?, '3101111111', ?, '2026-01-01', '01', 50000.0)
                """, (f'doc_{i}', num))

            # Factura al día (vence en 15 días)
            conn.execute("""
                INSERT INTO accounts_receivable (id, company_id, document_id, customer_id, customer_nombre,
                    monto_original, monto_abonado, saldo_pendiente, fecha_emision, fecha_vencimiento, estado)
                VALUES ('ar_1', '3101111111', 'doc_1', ?, 'Cliente Deudor', 50000.0, 0.0, 50000.0,
                    ?, ?, 'pendiente')
            """, (cli['id'], str(today - timedelta(days=15)), str(today + timedelta(days=15))))

            # Factura con 20 días de mora (cubo 1-30)
            conn.execute("""
                INSERT INTO accounts_receivable (id, company_id, document_id, customer_id, customer_nombre,
                    monto_original, monto_abonado, saldo_pendiente, fecha_emision, fecha_vencimiento, estado)
                VALUES ('ar_2', '3101111111', 'doc_2', ?, 'Cliente Deudor', 30000.0, 0.0, 30000.0,
                    ?, ?, 'pendiente')
            """, (cli['id'], str(today - timedelta(days=50)), str(today - timedelta(days=20))))

            # Factura con 100 días de mora (cubo +90)
            conn.execute("""
                INSERT INTO accounts_receivable (id, company_id, document_id, customer_id, customer_nombre,
                    monto_original, monto_abonado, saldo_pendiente, fecha_emision, fecha_vencimiento, estado)
                VALUES ('ar_3', '3101111111', 'doc_3', ?, 'Cliente Deudor', 20000.0, 0.0, 20000.0,
                    ?, ?, 'pendiente')
            """, (cli['id'], str(today - timedelta(days=130)), str(today - timedelta(days=100))))

        res = self.c.get('/api/ventas/reports/cxc-aging?company_id=3101111111', headers=self.h_con)
        self.assertEqual(res.status_code, 200)
        data = res.get_json()['data']

        self.assertEqual(data['total_cartera_pendiente'], 100000.0)
        self.assertEqual(data['total_documentos_pendientes'], 3)

        cubos = data['cubos_antiguedad']
        self.assertEqual(cubos['al_dia']['monto'], 50000.0)
        self.assertEqual(cubos['dias_1_30']['monto'], 30000.0)
        self.assertEqual(cubos['mas_90']['monto'], 20000.0)

    # ── 5. Reporte de Auditoría de Cierres de Turno de Caja POS ───────
    def test_05_reporte_cierres_caja_arqueos_diferencias(self):
        shift = gestion_service.open_pos_shift('3101111111', '001', '00001', 'cajero_alfa', 20000.0)
        p = comercial_service.create_product('3101111111', {
            'codigo_interno': 'POS1', 'nombre': 'Art', 'codigo_cabys': '1234567890123', 'precio_venta': 10000.0
        }, 'admin_alfa')
        gestion_service.quick_pos_sale('3101111111', shift['id'], {
            'medio_pago': '01',
            'monto_recibido': 11300.0,
            'lineas': [{'product_id': p['id'], 'cantidad': 1, 'precio_unitario': 10000.0, 'tarifa_iva': 13.0}]
        }, 'cajero_alfa')
        gestion_service.close_pos_shift('3101111111', shift['id'], 30800.0, 'Faltante de 500 colones', 'cajero_alfa')

        res = self.c.get('/api/ventas/reports/pos-shifts?company_id=3101111111', headers=self.h_a)
        self.assertEqual(res.status_code, 200)
        data = res.get_json()['data']
        self.assertEqual(data['total_turnos'], 1)
        self.assertEqual(data['resumen']['total_ventas_efectivo'], 11300.0)
        self.assertEqual(data['resumen']['diferencia_neta_arqueo'], -500.0)

    # ── 6. Exportación a CSV de Todos los Reportes ───────────────────
    def test_06_exportacion_csv_todos_los_reportes(self):
        tipos = ['d104', 'sales', 'profitability', 'cxc_aging', 'pos_shifts']
        for t in tipos:
            res = self.c.get(f'/api/ventas/reports/export?company_id=3101111111&type={t}&format=csv', headers=self.h_a)
            self.assertEqual(res.status_code, 200, f"Fallo al exportar CSV de {t}")
            self.assertIn('text/csv', res.headers.get('Content-Type', ''))
            content = res.get_data(as_text=True)
            self.assertIn('Empresa,3101111111', content)

    # ── 7. Exportación a PDF de Reportes ─────────────────────────────
    def test_07_exportacion_pdf_reportes(self):
        tipos = ['d104', 'sales', 'profitability', 'cxc_aging', 'pos_shifts']
        for t in tipos:
            res = self.c.get(f'/api/ventas/reports/export?company_id=3101111111&type={t}&format=pdf', headers=self.h_a)
            self.assertEqual(res.status_code, 200, f"Fallo al exportar PDF de {t}")
            self.assertIn('application/pdf', res.headers.get('Content-Type', ''))
            data_bytes = res.get_data()
            self.assertTrue(data_bytes.startswith(b'%PDF'), f"Encabezado PDF inválido para {t}")

    # ── 8. Seguridad de Roles y Aislamiento Multiempresa ─────────────
    def test_08_seguridad_roles_y_aislamiento_multiempresa_reportes(self):
        # Admin y Contador acceden a reportes
        r_admin = self.c.get('/api/ventas/reports/d104?company_id=3101111111', headers=self.h_a)
        self.assertEqual(r_admin.status_code, 200)
        r_con = self.c.get('/api/ventas/reports/d104?company_id=3101111111', headers=self.h_con)
        self.assertEqual(r_con.status_code, 200)

        # Vendedor y Cajero reciben 403
        r_ven = self.c.get('/api/ventas/reports/d104?company_id=3101111111', headers=self.h_ven)
        self.assertEqual(r_ven.status_code, 403)
        r_caj = self.c.get('/api/ventas/reports/d104?company_id=3101111111', headers=self.h_caj)
        self.assertEqual(r_caj.status_code, 403)

        # Dueño de Beta recibe 403 al consultar reportes de Alfa
        r_beta = self.c.get('/api/ventas/reports/d104?company_id=3101111111', headers=self.h_b)
        self.assertEqual(r_beta.status_code, 403)

        # Sin token recibe 401
        r_anon = self.c.get('/api/ventas/reports/d104?company_id=3101111111')
        self.assertEqual(r_anon.status_code, 401)

    # ── 9. Integración en UI HTML (Pantalla de Reportes y Exportación) ───────────
    def test_09_estructura_ui_html_reportes(self):
        """Verifica que FACTURAMI.html cuente con la interfaz completa de Reportes Comerciales y Fiscales."""
        html_path = os.path.join(os.path.dirname(__file__), '..', 'FACTURAMI.html')
        self.assertTrue(os.path.exists(html_path), "No se encontró FACTURAMI.html")
        with open(html_path, 'r', encoding='utf-8') as f:
            html = f.read()

        # Página y navegación
        self.assertIn('id="vpg-v-reportes"', html)
        self.assertIn('id="v-reportes-dynamic-content"', html)
        self.assertIn("pageId === 'v-reportes'", html)
        self.assertIn('vLoadReportes()', html)

        # Tabs de selección de reporte
        self.assertIn('id="btn-rep-d104"', html)
        self.assertIn('id="btn-rep-sales"', html)
        self.assertIn('id="btn-rep-profitability"', html)
        self.assertIn('id="btn-rep-cxc_aging"', html)
        self.assertIn('id="btn-rep-pos_shifts"', html)

        # Filtros y contenedor
        self.assertIn('id="v_rep_fecha_inicio"', html)
        self.assertIn('id="v_rep_fecha_fin"', html)
        self.assertIn('id="v_report_result_container"', html)

        # Funciones JS de Fase 8
        self.assertIn('function vLoadReportes(', html)
        self.assertIn('function vSelectReportTab(', html)
        self.assertIn('function vFetchActiveReport(', html)
        self.assertIn('function vRenderReportD104(', html)
        self.assertIn('function vRenderReportSales(', html)
        self.assertIn('function vRenderReportProfitability(', html)
        self.assertIn('function vRenderReportCxCAging(', html)
        self.assertIn('function vRenderReportPosShifts(', html)
        self.assertIn('function vExportActiveReport(', html)


if __name__ == '__main__':
    unittest.main()
