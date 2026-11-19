"""
Tests FASE 4: Administración Comercial (Clientes, Productos, Categorías, CABYS, Impuestos).
Todos los datos son ficticios. Base de datos temporal aislada.
"""
import os
import tempfile
import unittest

os.environ.setdefault('JWT_SECRET_KEY', 'test-jwt-secret-key-0123456789-abcdef')
os.environ.setdefault('N8N_INTERNAL_SECRET', 'test-n8n-secret-key-0123456789-abcdef')
os.environ['FACTURAMI_FE_SECRET_KEY'] = 'test-fe-secret-key-0123456789-abcdefghij'

from flask_jwt_extended import create_access_token
import db as database
from app import create_app


class TestFase4VentasComercial(unittest.TestCase):
    def setUp(self):
        fd, self.tmp_db = tempfile.mkstemp(suffix='.db')
        os.close(fd)
        self.prev_db_path = database.DB_PATH
        database.DB_PATH = self.tmp_db
        database.init_db(self.tmp_db)
        self.app = create_app()
        self.c = self.app.test_client()

        with database.get_db(self.tmp_db) as conn:
            # Cuentas ficticias
            for u in ('dueno_a', 'dueno_b', 'vendedor_a', 'cajero_a', 'contador_a'):
                database.upsert_cuenta(conn, {'username': u, 'nombre': u, 'role': 'contador', 'activo': 1})
            # Dos empresas aisladas
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
            self.h_cont = tok('contador_a')

        # Asignar roles internos en Empresa Alfa
        self.c.post('/api/ventas/users', headers=self.h_a,
                    json={'company_id': '3101111111', 'username': 'vendedor_a', 'role': 'vendedor'})
        self.c.post('/api/ventas/users', headers=self.h_a,
                    json={'company_id': '3101111111', 'username': 'cajero_a', 'role': 'cajero'})
        self.c.post('/api/ventas/users', headers=self.h_a,
                    json={'company_id': '3101111111', 'username': 'contador_a', 'role': 'contador'})

    def tearDown(self):
        database.DB_PATH = self.prev_db_path
        try:
            os.remove(self.tmp_db)
        except OSError:
            pass

    # ── Validación de Identificación de Costa Rica ────────────────────
    def test_01_validacion_identificacion_clientes_cr(self):
        url = '/api/ventas/customers'
        # Cédula física válida (9 dígitos)
        r = self.c.post(url, headers=self.h_a, json={
            'company_id': '3101111111', 'tipo_identificacion': '01',
            'identificacion': '1-0234-0567', 'nombre': 'Cliente Físico Válido'
        })
        self.assertEqual(r.status_code, 200)
        c = r.get_json()['data']
        self.assertEqual(c['identificacion'], '102340567')

        # Cédula jurídica válida (10 dígitos)
        r = self.c.post(url, headers=self.h_a, json={
            'company_id': '3101111111', 'tipo_identificacion': '02',
            'identificacion': '3-101-123456', 'nombre': 'Corporación Jurídica Válida'
        })
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()['data']['identificacion'], '3101123456')

        # DIMEX válido (11 o 12 dígitos)
        r = self.c.post(url, headers=self.h_a, json={
            'company_id': '3101111111', 'tipo_identificacion': '03',
            'identificacion': '123456789012', 'nombre': 'Residente DIMEX'
        })
        self.assertEqual(r.status_code, 200)

        # Cédula física con menos de 9 dígitos -> rechazo 400
        r = self.c.post(url, headers=self.h_a, json={
            'company_id': '3101111111', 'tipo_identificacion': '01',
            'identificacion': '12345678', 'nombre': 'Cédula Corta Inválida'
        })
        self.assertEqual(r.status_code, 400)

        # Cédula jurídica con 9 dígitos -> rechazo 400
        r = self.c.post(url, headers=self.h_a, json={
            'company_id': '3101111111', 'tipo_identificacion': '02',
            'identificacion': '310112345', 'nombre': 'Jurídica Corta Inválida'
        })
        self.assertEqual(r.status_code, 400)

        # Correo con formato inválido -> rechazo 400
        r = self.c.post(url, headers=self.h_a, json={
            'company_id': '3101111111', 'tipo_identificacion': '01',
            'identificacion': '203330444', 'nombre': 'Test Correo Mal', 'email': 'no-es-correo'
        })
        self.assertEqual(r.status_code, 400)

    # ── Aislamiento Multiempresa de Clientes ────────────────────────────
    def test_02_aislamiento_multiempresa_clientes(self):
        # Crear cliente en Empresa Alfa
        r_a = self.c.post('/api/ventas/customers', headers=self.h_a, json={
            'company_id': '3101111111', 'tipo_identificacion': '01',
            'identificacion': '109990888', 'nombre': 'Cliente Solo De Alfa',
            'limite_credito': 500000, 'plazo_credito': 30
        })
        self.assertEqual(r_a.status_code, 200)
        c_a_id = r_a.get_json()['data']['id']

        # Empresa Beta NO puede ver el cliente de Alfa en el listado
        r_list_b = self.c.get('/api/ventas/customers?company_id=3101222222', headers=self.h_b)
        self.assertEqual(r_list_b.status_code, 200)
        self.assertEqual(r_list_b.get_json()['data']['total'], 0)

        # Empresa Beta NO puede consultar por ID el cliente de Alfa
        r_get_b = self.c.get(f'/api/ventas/customers/{c_a_id}?company_id=3101222222', headers=self.h_b)
        self.assertEqual(r_get_b.status_code, 404)

        # Empresa Beta NO puede acceder al cliente usando company_id ajeno -> 403
        r_hack = self.c.get(f'/api/ventas/customers/{c_a_id}?company_id=3101111111', headers=self.h_b)
        self.assertEqual(r_hack.status_code, 403)

        # Empresa Beta SÍ puede registrar el mismo número de cédula en su propia empresa (aislamiento independiente)
        r_b = self.c.post('/api/ventas/customers', headers=self.h_b, json={
            'company_id': '3101222222', 'tipo_identificacion': '01',
            'identificacion': '109990888', 'nombre': 'Cliente en Beta'
        })
        self.assertEqual(r_b.status_code, 200)

        # Pero dentro de Alfa, duplicar la misma cédula activa da error 400
        r_dup = self.c.post('/api/ventas/customers', headers=self.h_a, json={
            'company_id': '3101111111', 'tipo_identificacion': '01',
            'identificacion': '109990888', 'nombre': 'Cliente Duplicado Alfa'
        })
        self.assertEqual(r_dup.status_code, 400)

    # ── Categorías de Productos ────────────────────────────────────────
    def test_03_gestion_categorias_productos(self):
        # Crear categoría en Alfa
        r = self.c.post('/api/ventas/categories', headers=self.h_a, json={
            'company_id': '3101111111', 'nombre': 'Electrónica y Cómputo', 'descripcion': 'Equipos y periféricos'
        })
        self.assertEqual(r.status_code, 200)
        cat_id = r.get_json()['data']['id']

        # Duplicado de nombre en la misma empresa da 400
        r_dup = self.c.post('/api/ventas/categories', headers=self.h_a, json={
            'company_id': '3101111111', 'nombre': 'electrónica y cómputo'
        })
        self.assertEqual(r_dup.status_code, 400)

        # Actualizar categoría
        r_up = self.c.put(f'/api/ventas/categories/{cat_id}', headers=self.h_a, json={
            'company_id': '3101111111', 'nombre': 'Tecnología', 'descripcion': 'Actualizada'
        })
        self.assertEqual(r_up.status_code, 200)
        self.assertEqual(r_up.get_json()['data']['nombre'], 'Tecnología')

        # Listado muestra la categoría
        cats = self.c.get('/api/ventas/categories?company_id=3101111111', headers=self.h_a).get_json()['data']
        self.assertEqual(len(cats), 1)
        self.assertEqual(cats[0]['nombre'], 'Tecnología')

    # ── Validación de CABYS y Catálogo de Productos ───────────────────
    def test_04_validacion_cabys_y_productos(self):
        # Crear categoría previa
        rc = self.c.post('/api/ventas/categories', headers=self.h_a, json={
            'company_id': '3101111111', 'nombre': 'Servicios Profesionales'
        })
        cat_id = rc.get_json()['data']['id']

        # Producto tipo mercancia con CABYS válido (13 dígitos) y tarifa IVA 13% ('08')
        r_prod = self.c.post('/api/ventas/products', headers=self.h_a, json={
            'company_id': '3101111111',
            'codigo_interno': 'LAPTOP-001',
            'codigo_cabys': '5411100000000',
            'nombre': 'Laptop Pro 15 pulgadas',
            'tipo': 'mercancia',
            'category_id': cat_id,
            'unidad_medida': 'Unid',
            'precio_compra': 400000,
            'precio_venta': 550000,
            'precio_mayorista': 500000,
            'tarifa_iva_codigo': '08',
            'existencia': 15,
            'stock_minimo': 5,
            'controla_inventario': 1
        })
        self.assertEqual(r_prod.status_code, 200)
        p = r_prod.get_json()['data']
        self.assertEqual(p['tarifa_iva_porcentaje'], 13.0)
        # 550000 * 1.13 = 621500.0
        self.assertEqual(p['precio_con_iva'], 621500.0)
        self.assertFalse(p['alerta_stock_bajo'])

        # CABYS inválido (menos de 13 dígitos) -> 400
        r_bad_cabys = self.c.post('/api/ventas/products', headers=self.h_a, json={
            'company_id': '3101111111',
            'codigo_interno': 'BAD-CABYS-01',
            'codigo_cabys': '123456789012',  # 12 dígitos
            'nombre': 'Producto Cabys Malo'
        })
        self.assertEqual(r_bad_cabys.status_code, 400)

        # Tarifa IVA exenta '01' debe registrar 0% de IVA
        r_exento = self.c.post('/api/ventas/products', headers=self.h_a, json={
            'company_id': '3101111111',
            'codigo_interno': 'SERV-EXENTO',
            'codigo_cabys': '8311100000000',
            'nombre': 'Servicio Educativo Exento',
            'tipo': 'servicio',
            'unidad_medida': 'Sp',
            'precio_venta': 80000,
            'tarifa_iva_codigo': '01'
        })
        self.assertEqual(r_exento.status_code, 200)
        p_ex = r_exento.get_json()['data']
        self.assertEqual(p_ex['tarifa_iva_porcentaje'], 0.0)
        self.assertEqual(p_ex['precio_con_iva'], 80000.0)
        # Servicios no controlan inventario
        self.assertEqual(p_ex['controla_inventario'], 0)

    # ── Control de Stock y Alertas de Stock Bajo ───────────────────────
    def test_05_control_stock_alertas_y_ajustes(self):
        r = self.c.post('/api/ventas/products', headers=self.h_a, json={
            'company_id': '3101111111',
            'codigo_interno': 'ITEM-STOCK',
            'codigo_cabys': '1234567890123',
            'nombre': 'Artículo con Stock Crítico',
            'tipo': 'mercancia',
            'unidad_medida': 'Unid',
            'precio_venta': 10000,
            'existencia': 4,
            'stock_minimo': 5,
            'controla_inventario': 1
        })
        self.assertEqual(r.status_code, 200)
        p = r.get_json()['data']
        pid = p['id']
        self.assertTrue(p['alerta_stock_bajo'])

        # Filtro de stock bajo en el listado
        r_list = self.c.get('/api/ventas/products?company_id=3101111111&stock_bajo=1', headers=self.h_a)
        self.assertEqual(r_list.status_code, 200)
        items = r_list.get_json()['data']['items']
        self.assertTrue(any(it['id'] == pid for it in items))

        # Ajuste manual de stock
        r_adj = self.c.post(f'/api/ventas/products/{pid}/stock', headers=self.h_a, json={
            'company_id': '3101111111', 'nuevo_stock': 20, 'motivo': 'Ingreso de mercadería por compra'
        })
        self.assertEqual(r_adj.status_code, 200)
        p_up = r_adj.get_json()['data']
        self.assertEqual(p_up['existencia'], 20.0)
        self.assertFalse(p_up['alerta_stock_bajo'])

    # ── Resumen Financiero y Crédito de Clientes ────────────────────────
    def test_06_resumen_financiero_y_limite_credito_cliente(self):
        r = self.c.post('/api/ventas/customers', headers=self.h_a, json={
            'company_id': '3101111111', 'tipo_identificacion': '02',
            'identificacion': '3101999999', 'nombre': 'Distribuidora Credito S.A.',
            'limite_credito': 1000000, 'plazo_credito': 30
        })
        cust_id = r.get_json()['data']['id']

        # Simular cuenta por cobrar pendiente en accounts_receivable
        with database.get_db(self.tmp_db) as conn:
            conn.execute("""
                INSERT INTO electronic_documents (id, company_id, customer_id, tipo_documento, consecutivo,
                                                 fecha_emision, estado_comprobante)
                VALUES ('doc-1', '3101111111', ?, '01', '00100001010000000001', '2026-01-01', 'aceptado')
            """, (cust_id,))
            conn.execute("""
                INSERT INTO accounts_receivable (id, company_id, document_id, customer_id, customer_nombre,
                                                 fecha_emision, fecha_vencimiento, monto_original, monto_abonado,
                                                 saldo_pendiente, estado)
                VALUES ('ar-1', '3101111111', 'doc-1', ?, 'Distribuidora Credito S.A.',
                        '2026-01-01', '2026-01-31', 300000, 0, 300000, 'pendiente')
            """, (cust_id,))

        # Consultar cliente: debe reflejar saldo_pendiente = 300000 y credito_disponible = 700000
        c_detail = self.c.get(f'/api/ventas/customers/{cust_id}?company_id=3101111111', headers=self.h_a).get_json()['data']
        self.assertEqual(c_detail['saldo_pendiente'], 300000.0)
        self.assertEqual(c_detail['credito_disponible'], 700000.0)

    # ── Roles y Permisos en Administración Comercial ───────────────────
    def test_07_roles_y_permisos_comercial(self):
        q = '?company_id=3101111111'
        # Vendedor: PUEDE ver clientes y crear/editar clientes
        r = self.c.post('/api/ventas/customers', headers=self.h_vend, json={
            'company_id': '3101111111', 'tipo_identificacion': '01',
            'identificacion': '108880777', 'nombre': 'Cliente Creado Por Vendedor'
        })
        self.assertEqual(r.status_code, 200)

        # Vendedor: PUEDE ver productos, pero NO PUEDE crear ni editar productos (403)
        self.assertEqual(self.c.get('/api/ventas/products' + q, headers=self.h_vend).status_code, 200)
        r_prod = self.c.post('/api/ventas/products', headers=self.h_vend, json={
            'company_id': '3101111111', 'codigo_interno': 'VEND-PROD', 'codigo_cabys': '1234567890123',
            'nombre': 'Intento Vendedor'
        })
        self.assertEqual(r_prod.status_code, 403)

        # Cajero: PUEDE ver productos y clientes, pero NO PUEDE crear clientes ni productos (403)
        self.assertEqual(self.c.get('/api/ventas/customers' + q, headers=self.h_caj).status_code, 200)
        self.assertEqual(self.c.get('/api/ventas/products' + q, headers=self.h_caj).status_code, 200)
        self.assertEqual(self.c.post('/api/ventas/customers', headers=self.h_caj, json={
            'company_id': '3101111111', 'tipo_identificacion': '01',
            'identificacion': '107770666', 'nombre': 'Intento Cajero'
        }).status_code, 403)

    # ── Eliminación Segura y Preservación de Historial ─────────────────
    def test_08_eliminacion_segura_con_preservacion_historial(self):
        # 1. Cliente sin historial -> Hard delete
        r1 = self.c.post('/api/ventas/customers', headers=self.h_a, json={
            'company_id': '3101111111', 'tipo_identificacion': '01',
            'identificacion': '101110222', 'nombre': 'Cliente Sin Historial'
        })
        c1_id = r1.get_json()['data']['id']
        del_res = self.c.delete(f'/api/ventas/customers/{c1_id}?company_id=3101111111', headers=self.h_a).get_json()['data']
        self.assertEqual(del_res['mode'], 'hard')

        # 2. Cliente con comprobantes emitidos -> Soft delete (inactivo)
        r2 = self.c.post('/api/ventas/customers', headers=self.h_a, json={
            'company_id': '3101111111', 'tipo_identificacion': '01',
            'identificacion': '103330444', 'nombre': 'Cliente Con Historial Fiscal'
        })
        c2_id = r2.get_json()['data']['id']
        with database.get_db(self.tmp_db) as conn:
            conn.execute("""
                INSERT INTO electronic_documents (id, company_id, customer_id, tipo_documento, consecutivo,
                                                 fecha_emision, estado_comprobante)
                VALUES ('doc-fact-1', '3101111111', ?, '01', '00100001010000000001', '2026-01-01', 'aceptado')
            """, (c2_id,))

        del_res2 = self.c.delete(f'/api/ventas/customers/{c2_id}?company_id=3101111111', headers=self.h_a).get_json()['data']
        self.assertEqual(del_res2['mode'], 'soft')
        # Verificar que el registro aún existe en la base pero está inactivo
        c2_check = self.c.get(f'/api/ventas/customers/{c2_id}?company_id=3101111111', headers=self.h_a).get_json()['data']
        self.assertEqual(c2_check['estado'], 'inactivo')

    # ── Catálogos Auxiliares (Tarifas IVA y Unidades) ───────────────────
    def test_09_catalogos_auxiliares_impuestos_y_unidades(self):
        r_tax = self.c.get('/api/ventas/tax-rates', headers=self.h_a)
        self.assertEqual(r_tax.status_code, 200)
        taxes = r_tax.get_json()['data']
        self.assertGreaterEqual(len(taxes), 8)
        codigos = [t['codigo'] for t in taxes]
        self.assertIn('08', codigos)  # General 13%
        self.assertIn('01', codigos)  # Exento 0%
        self.assertIn('04', codigos)  # Reducida 4%

        r_units = self.c.get('/api/ventas/units', headers=self.h_a)
        self.assertEqual(r_units.status_code, 200)
        units = r_units.get_json()['data']
        self.assertTrue(any(u['codigo'] == 'Unid' for u in units))
        self.assertTrue(any(u['codigo'] == 'Sp' for u in units))

    # ── Integración de Interfaz de Usuario en FACTURAMI.html ───────────
    def test_10_estructura_ui_html_modales_y_funciones(self):
        html_path = os.path.join(os.path.dirname(__file__), '..', 'FACTURAMI.html')
        with open(html_path, 'r', encoding='utf-8') as f:
            html = f.read()

        # Comprobar páginas comerciales
        self.assertIn('id="vpg-v-clientes"', html)
        self.assertIn('id="vpg-v-productos"', html)

        # Comprobar modales dialog de Fase 4
        self.assertIn('id="vModalCliente"', html)
        self.assertIn('id="vModalClienteDetalle"', html)
        self.assertIn('id="vModalProducto"', html)
        self.assertIn('id="vModalCategorias"', html)
        self.assertIn('id="vModalStock"', html)

        # Comprobar inputs y validadores de Costa Rica en UI
        self.assertIn('id="v_cli_tipo_id"', html)
        self.assertIn('id="v_cli_num_id"', html)
        self.assertIn('id="v_p_cabys"', html)
        self.assertIn('id="v_p_iva_cod"', html)

        # Comprobar funciones JS de Fase 4
        for fn in ('vLoadClientes', 'vSaveCliente', 'vShowClienteDetalle', 'vDeleteCliente',
                   'vLoadProductos', 'vSaveProducto', 'vCalcMargen', 'vCalcPrecioFromMargen',
                   'vOpenCategoriasModal', 'vAddCategoria', 'vDeleteCategoria',
                   'vOpenStockModal', 'vSaveStockAjuste'):
            self.assertIn(f'function {fn}', html)


if __name__ == '__main__':
    unittest.main()
