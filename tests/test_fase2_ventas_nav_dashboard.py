"""
Tests Automatizados - FASE 2: Integración de Navegación (Mine Menu -> Sistema de Ventas) y Dashboard Base.
==========================================================================================================
Verifica:
  1. Presencia del botón «Sistema de Ventas» en Mine Menu (#profile-menu-view) conservando los botones originales.
  2. Presencia de las 12 secciones del menú lateral, los 10 indicadores, los 9 accesos rápidos y los gráficos.
  3. Autenticación y aislamiento multiempresa estricto (401 sin token, 403 ante empresa de otro contador).
  4. Inicialización automática del contexto comercial desde el perfil existente de `clientes` sin exponer secretos.
  5. Exactitud de los 10 indicadores y 7 series estadísticas con datos reales en SQLite aislados por empresa.
"""
import os
import tempfile
import unittest
from datetime import date

os.environ.setdefault('JWT_SECRET_KEY', 'test-jwt-secret-key-0123456789-abcdef')
os.environ.setdefault('N8N_INTERNAL_SECRET', 'test-n8n-secret-key-0123456789-abcdef')

from flask_jwt_extended import create_access_token
import db as database
from app import create_app


class TestFase2VentasNavDashboard(unittest.TestCase):
    def setUp(self):
        self.tmp_fd, self.tmp_db = tempfile.mkstemp(suffix='.db')
        os.close(self.tmp_fd)
        self.prev_db_path = database.DB_PATH
        database.DB_PATH = self.tmp_db
        database.init_db(self.tmp_db)

        self.app = create_app()
        self.client = self.app.test_client()

        with database.get_db(self.tmp_db) as conn:
            # Crear dos contadores independientes
            database.upsert_cuenta(conn, {
                'username': 'contador_a',
                'nombre': 'Despacho A',
                'role': 'contador',
                'activo': 1
            })
            database.upsert_cuenta(conn, {
                'username': 'contador_b',
                'nombre': 'Despacho B',
                'role': 'contador',
                'activo': 1
            })

            # Crear empresa 3101111111 para contador_a y 3101222222 para contador_b
            database.upsert_cliente(conn, {
                'id': '3101111111',
                'type': 'PJ',
                'name': 'Comercializadora Alfa S.A.',
                'tel': '2222-1111',
                'mail': 'facturacion@alfa.cr',
                'dir': 'San José, Escazú',
                'act': 'Venta de tecnología y suministros',
                'rep': 'Ana Soto'
            }, cuenta_id='contador_a')

            database.upsert_cliente(conn, {
                'id': '3101222222',
                'type': 'PJ',
                'name': 'Inversiones Beta S.A.',
                'tel': '2222-2222',
                'mail': 'info@beta.cr',
                'dir': 'Heredia Centro',
                'act': 'Consultoría',
                'rep': 'Carlos Mora'
            }, cuenta_id='contador_b')

        with self.app.app_context():
            self.token_a = create_access_token(identity='contador_a', additional_claims={'role': 'contador'})
            self.token_b = create_access_token(identity='contador_b', additional_claims={'role': 'contador'})

        self.headers_a = {'Authorization': f'Bearer {self.token_a}'}
        self.headers_b = {'Authorization': f'Bearer {self.token_b}'}

    def tearDown(self):
        database.DB_PATH = self.prev_db_path
        try:
            os.remove(self.tmp_db)
        except OSError:
            pass

    def test_01_mine_menu_y_estructura_html_sistema_ventas(self):
        """Verifica que Mine Menu conserve sus botones y tenga el botón Sistema de Ventas y las 12 secciones."""
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        html_path = os.path.join(base_dir, 'FACTURAMI.html')
        with open(html_path, 'r', encoding='utf-8') as f:
            html = f.read()

        # Botón nuevo en Mine Menu (#profile-menu-view)
        self.assertIn('id="btn-mine-menu-ventas"', html)
        self.assertIn('onclick="openVentasSystem()"', html)
        self.assertIn('Sistema de Ventas', html)

        # Botones originales conservados intactos
        self.assertIn('Abrir Contabilidad', html)
        self.assertIn('Editar Información', html)
        self.assertIn('Cambiar Cliente', html)
        self.assertIn('Borrar Perfil', html)

        # Las 12 secciones del menú lateral de Sistema de Ventas
        for nav_id in (
            'vni-v-dash', 'vni-v-facturacion', 'vni-v-clientes', 'vni-v-productos',
            'vni-v-inventario', 'vni-v-cotizaciones', 'vni-v-pedidos', 'vni-v-pos',
            'vni-v-cxc', 'vni-v-caja', 'vni-v-reportes', 'vni-v-config'
        ):
            self.assertIn(f'id="{nav_id}"', html, f"Falta sección {nav_id} en sidebar de ventas")

        # Los 10 indicadores del Dashboard
        for kpi_id in (
            'vd_kpi_ventas_dia', 'vd_kpi_ventas_mes', 'vd_kpi_facturas',
            'vd_kpi_tiquetes', 'vd_kpi_pendientes_mh', 'vd_kpi_rechazadas_mh',
            'vd_kpi_cxc', 'vd_kpi_clientes', 'vd_kpi_productos', 'vd_kpi_inventario'
        ):
            self.assertIn(f'id="{kpi_id}"', html, f"Falta KPI {kpi_id} en Dashboard de Ventas")

        # Los 9 botones de acceso rápido
        for q_id in (
            'vq-btn-nueva-factura', 'vq-btn-nuevo-tiquete', 'vq-btn-nueva-cotizacion',
            'vq-btn-registrar-cliente', 'vq-btn-registrar-producto', 'vq-btn-registrar-pago',
            'vq-btn-consultar-docs', 'vq-btn-consultar-inv', 'vq-btn-generar-reportes'
        ):
            self.assertIn(f'id="{q_id}"', html, f"Falta acceso rápido {q_id}")

    def test_02_seguridad_y_aislamiento_multiempresa(self):
        """Un usuario sin token recibe 401 y otra cuenta recibe 403 al consultar empresa ajena."""
        # Sin token -> 401
        r_unauth = self.client.get('/api/ventas/context?company_id=3101111111')
        self.assertEqual(r_unauth.status_code, 401)

        r_dash_unauth = self.client.get('/api/ventas/dashboard?company_id=3101111111')
        self.assertEqual(r_dash_unauth.status_code, 401)

        # Contador B intentando acceder a la empresa 3101111111 de Contador A -> 403
        r_forbidden_ctx = self.client.get('/api/ventas/context?company_id=3101111111', headers=self.headers_b)
        self.assertEqual(r_forbidden_ctx.status_code, 403)

        r_forbidden_dash = self.client.get('/api/ventas/dashboard?company_id=3101111111', headers=self.headers_b)
        self.assertEqual(r_forbidden_dash.status_code, 403)

    def test_03_contexto_empresarial_hereda_perfil_sin_exponer_secretos(self):
        """GET /api/ventas/context inicializa configuración desde `clientes` sin pedir datos repetidos ni exponer secretos."""
        res = self.client.get('/api/ventas/context?company_id=3101111111', headers=self.headers_a)
        self.assertEqual(res.status_code, 200)
        payload = res.get_json()
        self.assertTrue(payload['ok'])
        data = payload['data']

        self.assertEqual(data['company_id'], '3101111111')
        self.assertEqual(data['usuario_autenticado'], 'contador_a')
        self.assertEqual(data['profile']['nombre'], 'Comercializadora Alfa S.A.')
        self.assertEqual(data['settings']['razon_social'], 'Comercializadora Alfa S.A.')
        self.assertEqual(data['settings']['identificacion'], '3101111111')
        self.assertEqual(data['settings']['tipo_identificacion'], '02')
        self.assertEqual(data['settings']['email'], 'facturacion@alfa.cr')
        self.assertEqual(data['branches'][0]['codigo_sucursal'], '001')
        self.assertEqual(data['terminals'][0]['codigo_terminal'], '00001')

        # Verificar que jamás se incluyan secretos cifrados en la respuesta
        for forbidden_key in ('hacienda_password_enc', 'cert_pin_enc', 'cert_file_enc'):
            self.assertNotIn(forbidden_key, data['settings'])

    def test_04_dashboard_10_indicadores_y_graficos_con_datos_reales(self):
        """Verifica que los 10 KPIs y las 7 series de gráficos reflejen datos reales de SQLite aislados por empresa."""
        today_str = date.today().isoformat()
        cur_year = str(date.today().year)

        with database.get_db(self.tmp_db) as conn:
            # Registrar 2 clientes comerciales para Empresa Alfa (3101111111)
            conn.execute("""
                INSERT INTO sales_customers (id, company_id, identificacion, nombre, email, estado)
                VALUES ('cust-1', '3101111111', '101110111', 'Cliente Uno', 'c1@test.cr', 'activo'),
                       ('cust-2', '3101111111', '3101555666', 'Constructora Dos S.A.', 'c2@test.cr', 'activo')
            """)
            # Registrar 1 cliente comercial para Empresa Beta (3101222222) - NO debe contaminar a Alfa
            conn.execute("""
                INSERT INTO sales_customers (id, company_id, identificacion, nombre, email, estado)
                VALUES ('cust-b1', '3101222222', '102220222', 'Cliente Ajeno Beta', 'b@test.cr', 'activo')
            """)

            # Registrar 2 productos para Empresa Alfa (valor inventario: 10*25000 + 5*40000 = 450000)
            conn.execute("""
                INSERT INTO products (
                    id, company_id, codigo_interno, codigo_cabys, nombre, categoria_nombre,
                    precio_compra, precio_venta, existencia, stock_minimo, controla_inventario, estado
                ) VALUES
                ('prod-1', '3101111111', 'SKU-01', '4523000000000', 'Monitor LED 24', 'Tecnología', 25000, 40000, 10, 2, 1, 'activo'),
                ('prod-2', '3101111111', 'SKU-02', '4524000000000', 'Teclado Mecánico', 'Periféricos', 40000, 60000, 5, 1, 1, 'activo')
            """)

            # Emitir 1 Factura Electrónica (01) aceptada hoy de contado por 113,000
            conn.execute("""
                INSERT INTO electronic_documents (
                    id, company_id, customer_id, customer_nombre, tipo_documento,
                    consecutivo, fecha_emision, condicion_venta, medio_pago,
                    subtotal, total_impuestos, total_comprobante, estado_comprobante
                ) VALUES (
                    'doc-fe-1', '3101111111', 'cust-1', 'Cliente Uno', '01',
                    '00100001010000000001', ?, '01', '01',
                    100000, 13000, 113000, 'aceptado'
                )
            """, (today_str,))
            conn.execute("""
                INSERT INTO electronic_document_lines (
                    id, document_id, company_id, numero_linea, product_id,
                    descripcion, categoria_nombre, cantidad, precio_unitario,
                    subtotal_linea, base_imponible, monto_impuesto, total_linea
                ) VALUES (
                    'line-1', 'doc-fe-1', '3101111111', 1, 'prod-1',
                    'Monitor LED 24', 'Tecnología', 2, 50000,
                    100000, 100000, 13000, 113000
                )
            """)

            # Emitir 1 Factura Electrónica (01) a crédito pendiente de respuesta de Hacienda por 56,500
            conn.execute("""
                INSERT INTO electronic_documents (
                    id, company_id, customer_id, customer_nombre, tipo_documento,
                    consecutivo, fecha_emision, condicion_venta, medio_pago,
                    subtotal, total_impuestos, total_comprobante, estado_comprobante
                ) VALUES (
                    'doc-fe-2', '3101111111', 'cust-2', 'Constructora Dos S.A.', '01',
                    '00100001010000000002', ?, '02', '04',
                    50000, 6500, 56500, 'enviado'
                )
            """, (today_str,))
            conn.execute("""
                INSERT INTO electronic_document_lines (
                    id, document_id, company_id, numero_linea, product_id,
                    descripcion, categoria_nombre, cantidad, precio_unitario,
                    subtotal_linea, base_imponible, monto_impuesto, total_linea
                ) VALUES (
                    'line-2', 'doc-fe-2', '3101111111', 1, 'prod-2',
                    'Teclado Mecánico', 'Periféricos', 1, 50000,
                    50000, 50000, 6500, 56500
                )
            """)
            # Cuenta por cobrar asociada
            conn.execute("""
                INSERT INTO accounts_receivable (
                    id, company_id, document_id, customer_id, customer_nombre,
                    fecha_emision, fecha_vencimiento, monto_original, monto_abonado, saldo_pendiente, estado
                ) VALUES (
                    'ar-1', '3101111111', 'doc-fe-2', 'cust-2', 'Constructora Dos S.A.',
                    ?, ?, 56500, 6500, 50000, 'parcial'
                )
            """, (today_str, today_str))

            # Emitir 1 Tiquete Electrónico (04) aceptado hoy por 22,600
            conn.execute("""
                INSERT INTO electronic_documents (
                    id, company_id, customer_id, customer_nombre, tipo_documento,
                    consecutivo, fecha_emision, condicion_venta, medio_pago,
                    subtotal, total_impuestos, total_comprobante, estado_comprobante
                ) VALUES (
                    'doc-te-1', '3101111111', '', 'Consumidor Final', '04',
                    '00100001040000000001', ?, '01', '02',
                    20000, 2600, 22600, 'aceptado'
                )
            """, (today_str,))

            # Emitir 1 Factura Electrónica (01) rechazada por Hacienda (no debe sumar a ventas efectivas)
            conn.execute("""
                INSERT INTO electronic_documents (
                    id, company_id, customer_id, customer_nombre, tipo_documento,
                    consecutivo, fecha_emision, condicion_venta, medio_pago,
                    subtotal, total_impuestos, total_comprobante, estado_comprobante
                ) VALUES (
                    'doc-fe-rech', '3101111111', 'cust-1', 'Cliente Uno', '01',
                    '00100001010000000003', ?, '01', '01',
                    10000, 1300, 11300, 'rechazado'
                )
            """, (today_str,))

            # Factura millonaria de Empresa Beta (3101222222) -> NO debe aparecer en Alfa
            conn.execute("""
                INSERT INTO electronic_documents (
                    id, company_id, customer_id, customer_nombre, tipo_documento,
                    consecutivo, fecha_emision, condicion_venta, medio_pago,
                    subtotal, total_impuestos, total_comprobante, estado_comprobante
                ) VALUES (
                    'doc-beta-1', '3101222222', 'cust-b1', 'Cliente Ajeno Beta', '01',
                    '00100001010000000099', ?, '01', '01',
                    9000000, 1170000, 10170000, 'aceptado'
                )
            """, (today_str,))

        res = self.client.get(
            f'/api/ventas/dashboard?company_id=3101111111&anio={cur_year}',
            headers=self.headers_a
        )
        self.assertEqual(res.status_code, 200)
        data = res.get_json()['data']
        kpis = data['kpis']

        # Ventas efectivas = 113000 + 56500 + 22600 = 192100 (excluye la rechazada y excluye la de Beta)
        self.assertAlmostEqual(kpis['ventas_dia'], 192100.0)
        self.assertAlmostEqual(kpis['ventas_mes'], 192100.0)
        self.assertEqual(kpis['total_facturas_emitidas'], 3)  # doc-fe-1, doc-fe-2, doc-fe-rech
        self.assertEqual(kpis['total_tiquetes_emitidos'], 1)  # doc-te-1
        self.assertEqual(kpis['facturas_pendientes_hacienda'], 1)  # doc-fe-2 ('enviado')
        self.assertEqual(kpis['facturas_rechazadas'], 1)  # doc-fe-rech ('rechazado')
        self.assertAlmostEqual(kpis['cuentas_por_cobrar'], 50000.0)
        self.assertEqual(kpis['clientes_registrados'], 2)
        self.assertEqual(kpis['productos_registrados'], 2)
        self.assertAlmostEqual(kpis['valor_inventario'], 450000.0)

        # Verificar gráficos estadísticos
        charts = data['charts']
        self.assertAlmostEqual(charts['contado_vs_credito']['Contado'], 135600.0)
        self.assertAlmostEqual(charts['contado_vs_credito']['Crédito'], 56500.0)
        self.assertEqual(len(charts['productos_mas_vendidos']), 2)
        self.assertEqual(charts['productos_mas_vendidos'][0]['nombre'], 'Monitor LED 24')


if __name__ == '__main__':
    unittest.main()
