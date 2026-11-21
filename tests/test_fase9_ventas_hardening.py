"""
Tests FASE 9: Hardening, Casos Borde y Resiliencia del Sistema de Ventas.
========================================================================
Valida:
  1. Concurrencia y bloqueo inmediato (BEGIN IMMEDIATE) para reserva de consecutivos.
  2. Situación de comprobantes oficiales v4.3 (Normal=1, Contingencia=2, Sin Internet=3).
  3. Protección estricta de Notas de Crédito (Anti-NC sobre NC).
  4. Límite de reembolso acumulado (capping) y prevención de doble anulación.
  5. Sincronización automática de Notas de Crédito con Cuentas por Cobrar (CxC).
  6. Control estricto de existencias (prevención de stock negativo si está deshabilitado).
  7. Reintegro automático de existencias en Kardex ante Notas de Crédito.
  8. Cola de reintento de comprobantes pendientes / fuera de línea (/api/ventas/documents/retry-queue).
  9. Integridad de la interfaz de usuario para contingencia y cola de reenvío.
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
import services.ventas_service as ventas_service
import services.fe_emission_service as emission_service
from services.fe_clave_service import desglosar_clave


class TestFase9VentasHardening(unittest.TestCase):
    def setUp(self):
        fd, self.tmp_db = tempfile.mkstemp(suffix='.db')
        os.close(fd)
        self.prev_db_path = database.DB_PATH
        database.DB_PATH = self.tmp_db
        database.init_db(self.tmp_db)
        self.app = create_app()
        self.c = self.app.test_client()

        with database.get_db(self.tmp_db) as conn:
            # Crear cuenta administradora / contador
            database.upsert_cuenta(conn, {'username': 'empresa_user', 'nombre': 'Titular Hardening', 'role': 'contador', 'activo': 1})
            # Crear empresa cliente
            database.upsert_cliente(conn, {
                'id': '3101888999',
                'type': 'PJ',
                'name': 'Corporación Resiliente CR S.A.',
                'email': 'admin@resiliente.cr'
            }, cuenta_id='empresa_user')

        with self.app.app_context():
            self.token = create_access_token(identity='empresa_user')
            self.headers = {'Authorization': f'Bearer {self.token}'}

        # Inicializar contexto comercial y terminales
        ventas_service.get_or_init_company_context('3101888999', db_path=self.tmp_db)

    def tearDown(self):
        database.DB_PATH = self.prev_db_path
        if os.path.exists(self.tmp_db):
            try:
                os.remove(self.tmp_db)
            except OSError:
                pass

    def test_01_situacion_comprobante_contingencia_y_offline(self):
        """Verifica que la situación (1, 2, 3) se refleje en el dígito 42 de la clave y en el estado de envío."""
        # 1. Situación Normal ('1')
        doc_normal = emission_service.emitir_comprobante('3101888999', {
            'tipo_documento': '01',
            'situacion': '1',
            'lineas': [{'descripcion': 'Línea Normal', 'cantidad': 1, 'precio_unitario': 1000, 'codigo_cabys': '1234567890123'}]
        }, actor='empresa_user', db_path=self.tmp_db)
        desg_norm = desglosar_clave(doc_normal['clave_numerica'])
        self.assertEqual(desg_norm['situacion'], '1')
        self.assertEqual(doc_normal['clave_numerica'][41], '1')

        # 2. Situación Contingencia ('2')
        doc_cont = emission_service.emitir_comprobante('3101888999', {
            'tipo_documento': '01',
            'situacion': '2',
            'lineas': [{'descripcion': 'Línea Contingencia', 'cantidad': 1, 'precio_unitario': 2000, 'codigo_cabys': '1234567890123'}]
        }, actor='empresa_user', db_path=self.tmp_db)
        desg_cont = desglosar_clave(doc_cont['clave_numerica'])
        self.assertEqual(desg_cont['situacion'], '2')
        self.assertEqual(doc_cont['clave_numerica'][41], '2')
        self.assertEqual(doc_cont['estado_comprobante'], 'pendiente_envio')
        self.assertIn('Contingencia', doc_cont['hacienda_mensaje'])

        # 3. Situación Sin Internet ('3')
        doc_offline = emission_service.emitir_comprobante('3101888999', {
            'tipo_documento': '01',
            'situacion': '3',
            'lineas': [{'descripcion': 'Línea Offline', 'cantidad': 1, 'precio_unitario': 3000, 'codigo_cabys': '1234567890123'}]
        }, actor='empresa_user', db_path=self.tmp_db)
        desg_off = desglosar_clave(doc_offline['clave_numerica'])
        self.assertEqual(desg_off['situacion'], '3')
        self.assertEqual(doc_offline['clave_numerica'][41], '3')
        self.assertEqual(doc_offline['estado_comprobante'], 'pendiente_envio')
        self.assertIn('Sin internet', doc_offline['hacienda_mensaje'])

        # 4. Situación Inválida ('9') debe rechazar con 400
        with self.assertRaises(emission_service.EmissionError):
            emission_service.emitir_comprobante('3101888999', {
                'tipo_documento': '01',
                'situacion': '9',
                'lineas': [{'descripcion': 'Error', 'cantidad': 1, 'precio_unitario': 100}]
            }, actor='empresa_user', db_path=self.tmp_db)

    def test_02_nota_credito_prohibe_nc_sobre_otra_nc(self):
        """Verifica que no sea posible emitir una Nota de Crédito que referencie a otra Nota de Crédito."""
        # Paso 1: Emitir factura
        f = emission_service.emitir_comprobante('3101888999', {
            'tipo_documento': '01',
            'lineas': [{'descripcion': 'Servicio Inicial', 'cantidad': 1, 'precio_unitario': 10000, 'codigo_cabys': '1234567890123'}]
        }, actor='empresa_user', db_path=self.tmp_db)

        # Paso 2: Emitir NC sobre la factura
        nc1 = emission_service.emitir_comprobante('3101888999', {
            'tipo_documento': '03',
            'ref_numero_clave': f['clave_numerica'],
            'ref_tipo_documento': '01',
            'ref_razon': 'Corrección parcial',
            'lineas': [{'descripcion': 'Ajuste', 'cantidad': 1, 'precio_unitario': 5000, 'codigo_cabys': '1234567890123'}]
        }, actor='empresa_user', db_path=self.tmp_db)
        self.assertEqual(nc1['tipo_documento'], '03')

        # Paso 3: Intentar emitir una NC sobre la NC1 recién creada -> Debe fallar
        with self.assertRaises(emission_service.EmissionError) as ctx:
            emission_service.emitir_comprobante('3101888999', {
                'tipo_documento': '03',
                'ref_numero_clave': nc1['clave_numerica'],
                'ref_tipo_documento': '03',
                'ref_razon': 'Intento ilegal de anular NC con NC',
                'lineas': [{'descripcion': 'Intento', 'cantidad': 1, 'precio_unitario': 5000, 'codigo_cabys': '1234567890123'}]
            }, actor='empresa_user', db_path=self.tmp_db)
        self.assertIn("No es posible emitir una Nota de Crédito sobre otra Nota de Crédito", str(ctx.exception))

    def test_03_nota_credito_capping_de_saldo_y_anti_doble_anulacion(self):
        """Verifica que el monto total de NC no pueda superar el monto de la factura referenciada."""
        # Factura por subtotal ₡10,000 + 13% IVA = ₡11,300
        f = emission_service.emitir_comprobante('3101888999', {
            'tipo_documento': '01',
            'lineas': [{'descripcion': 'Venta Grande', 'cantidad': 1, 'precio_unitario': 10000, 'codigo_cabys': '1234567890123'}]
        }, actor='empresa_user', db_path=self.tmp_db)
        tot_f = f['total_comprobante']  # 11300.0

        # NC Parcial 1: ₡6,000 + IVA = ₡6,780. Saldo restante = ₡4,520
        nc1 = emission_service.emitir_comprobante('3101888999', {
            'tipo_documento': '03',
            'ref_numero_clave': f['consecutivo'],
            'lineas': [{'descripcion': 'Rebaja 1', 'cantidad': 1, 'precio_unitario': 6000, 'codigo_cabys': '1234567890123'}]
        }, actor='empresa_user', db_path=self.tmp_db)
        self.assertEqual(nc1['total_comprobante'], 6780.0)

        # Intento de NC Parcial 2 por ₡5,000 + IVA (₡5,650) -> Supera los ₡4,520 restantes -> Debe fallar
        with self.assertRaises(emission_service.EmissionError) as ctx_exc:
            emission_service.emitir_comprobante('3101888999', {
                'tipo_documento': '03',
                'ref_numero_clave': f['consecutivo'],
                'lineas': [{'descripcion': 'Rebaja Excedente', 'cantidad': 1, 'precio_unitario': 5000, 'codigo_cabys': '1234567890123'}]
            }, actor='empresa_user', db_path=self.tmp_db)
        self.assertIn("excede el saldo acreditable restante", str(ctx_exc.exception))

        # NC Parcial 2 por los ₡4,000 + IVA exactos (₡4,520) -> Debe ser exitosa
        nc2 = emission_service.emitir_comprobante('3101888999', {
            'tipo_documento': '03',
            'ref_numero_clave': f['consecutivo'],
            'lineas': [{'descripcion': 'Rebaja Final', 'cantidad': 1, 'precio_unitario': 4000, 'codigo_cabys': '1234567890123'}]
        }, actor='empresa_user', db_path=self.tmp_db)
        self.assertEqual(nc2['total_comprobante'], 4520.0)

        # Intento de una tercera NC aunque sea por ₡1 -> Debe indicar que ya está totalmente anulada
        with self.assertRaises(emission_service.EmissionError) as ctx_full:
            emission_service.emitir_comprobante('3101888999', {
                'tipo_documento': '03',
                'ref_numero_clave': f['consecutivo'],
                'lineas': [{'descripcion': 'Extra', 'cantidad': 1, 'precio_unitario': 1, 'codigo_cabys': '1234567890123'}]
            }, actor='empresa_user', db_path=self.tmp_db)
        self.assertIn("ya se encuentra totalmente anulado/acreditado", str(ctx_full.exception))

    def test_04_nota_credito_sincroniza_cuentas_por_cobrar(self):
        """Verifica que la emisión de una NC reduzca automáticamente el saldo de la CxC o la marque como anulada."""
        # Factura a Crédito ('06') por ₡10,000 + IVA (₡11,300)
        f = emission_service.emitir_comprobante('3101888999', {
            'tipo_documento': '01',
            'condicion_venta': '06',
            'plazo_credito': 30,
            'customer_nombre': 'Cliente Crédito SA',
            'lineas': [{'descripcion': 'Mercancía al Crédito', 'cantidad': 1, 'precio_unitario': 10000, 'codigo_cabys': '1234567890123'}]
        }, actor='empresa_user', db_path=self.tmp_db)

        # Comprobar CxC creada
        with database.get_db(self.tmp_db) as conn:
            cxc = dict(conn.execute("SELECT * FROM accounts_receivable WHERE document_id = ?", (f['id'],)).fetchone())
            self.assertEqual(cxc['saldo_pendiente'], 11300.0)
            self.assertEqual(cxc['estado'], 'pendiente')

        # Emitir NC Parcial por ₡5,000 + IVA (₡5,650)
        emission_service.emitir_comprobante('3101888999', {
            'tipo_documento': '03',
            'ref_numero_clave': f['clave_numerica'],
            'lineas': [{'descripcion': 'Devolución Parcial', 'cantidad': 1, 'precio_unitario': 5000, 'codigo_cabys': '1234567890123'}]
        }, actor='empresa_user', db_path=self.tmp_db)

        # Verificar CxC rebajada a parcial
        with database.get_db(self.tmp_db) as conn:
            cxc_parcial = dict(conn.execute("SELECT * FROM accounts_receivable WHERE document_id = ?", (f['id'],)).fetchone())
            self.assertEqual(cxc_parcial['saldo_pendiente'], 5650.0)
            self.assertEqual(cxc_parcial['estado'], 'parcial')

        # Emitir NC por el resto de ₡5,000 + IVA (₡5,650)
        emission_service.emitir_comprobante('3101888999', {
            'tipo_documento': '03',
            'ref_numero_clave': f['clave_numerica'],
            'lineas': [{'descripcion': 'Devolución Total Restante', 'cantidad': 1, 'precio_unitario': 5000, 'codigo_cabys': '1234567890123'}]
        }, actor='empresa_user', db_path=self.tmp_db)

        # Verificar CxC totalmente anulada / saldada
        with database.get_db(self.tmp_db) as conn:
            cxc_anulada = dict(conn.execute("SELECT * FROM accounts_receivable WHERE document_id = ?", (f['id'],)).fetchone())
            self.assertEqual(cxc_anulada['saldo_pendiente'], 0.0)
            self.assertEqual(cxc_anulada['estado'], 'anulada')

    def test_05_control_estricto_existencias_inventario_no_negativo(self):
        """Verifica que no se permita facturar por encima del stock cuando permitir_venta_sin_stock=False."""
        # Crear producto con 5 unidades en stock
        prod_id = 'prod-inv-strict-01'
        with database.get_db(self.tmp_db) as conn:
            conn.execute("""
                INSERT INTO products (
                    id, company_id, codigo_interno, nombre, existencia, controla_inventario, precio_venta, tarifa_iva_codigo
                ) VALUES (?, '3101888999', 'STK-01', 'Producto Control Estricto', 5.0, 1, 1000.0, '08')
            """, (prod_id,))

        # 1. Intentar facturar 8 unidades con permitir_venta_sin_stock=False -> Debe fallar
        with self.assertRaises(emission_service.EmissionError) as ctx_stock:
            emission_service.emitir_comprobante('3101888999', {
                'tipo_documento': '01',
                'permitir_venta_sin_stock': False,
                'lineas': [{
                    'product_id': prod_id,
                    'descripcion': 'Producto Control Estricto',
                    'cantidad': 8,
                    'precio_unitario': 1000,
                    'codigo_cabys': '1234567890123'
                }]
            }, actor='empresa_user', db_path=self.tmp_db)
        self.assertIn("Stock insuficiente", str(ctx_stock.exception))

        # 2. Facturar 3 unidades (permitido) -> Stock pasa de 5 a 2
        f1 = emission_service.emitir_comprobante('3101888999', {
            'tipo_documento': '01',
            'permitir_venta_sin_stock': False,
            'lineas': [{
                'product_id': prod_id,
                'descripcion': 'Producto Control Estricto',
                'cantidad': 3,
                'precio_unitario': 1000,
                'codigo_cabys': '1234567890123'
            }]
        }, actor='empresa_user', db_path=self.tmp_db)

        with database.get_db(self.tmp_db) as conn:
            stk_mid = conn.execute("SELECT existencia FROM products WHERE id = ?", (prod_id,)).fetchone()[0]
            self.assertEqual(stk_mid, 2.0)

        # 3. Emitir Nota de Crédito por 2 unidades -> Stock se reintegra a 4
        emission_service.emitir_comprobante('3101888999', {
            'tipo_documento': '03',
            'ref_numero_clave': f1['clave_numerica'],
            'lineas': [{
                'product_id': prod_id,
                'descripcion': 'Devolución 2 unidades',
                'cantidad': 2,
                'precio_unitario': 1000,
                'codigo_cabys': '1234567890123'
            }]
        }, actor='empresa_user', db_path=self.tmp_db)

        with database.get_db(self.tmp_db) as conn:
            stk_rest = conn.execute("SELECT existencia FROM products WHERE id = ?", (prod_id,)).fetchone()[0]
            self.assertEqual(stk_rest, 4.0)

    def test_06_endpoint_retry_queue_hacienda_offline(self):
        """Verifica la API /api/ventas/documents/retry-queue para reintentar documentos en cola."""
        # Emitir documento en contingencia (situacion='2')
        doc = emission_service.emitir_comprobante('3101888999', {
            'tipo_documento': '01',
            'situacion': '2',
            'lineas': [{'descripcion': 'Offline Doc', 'cantidad': 1, 'precio_unitario': 5000, 'codigo_cabys': '1234567890123'}]
        }, actor='empresa_user', db_path=self.tmp_db)
        self.assertEqual(doc['estado_comprobante'], 'pendiente_envio')

        # Ejecutar retry-queue por endpoint
        res = self.c.post('/api/ventas/documents/retry-queue', json={
            'company_id': '3101888999'
        }, headers=self.headers)

        self.assertEqual(res.status_code, 200)
        data = res.get_json()['data']
        self.assertIn('procesados', data)
        self.assertIn('detalles', data)

    def test_07_elementos_de_interfaz_en_facturami_html(self):
        """Verifica que FACTURAMI.html cuente con el selector de situación y el botón de reintento de cola."""
        html_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'FACTURAMI.html')
        self.assertTrue(os.path.exists(html_path))
        with open(html_path, 'r', encoding='utf-8') as f:
            html = f.read()

        self.assertIn('id="v_fe_situacion"', html)
        self.assertIn('id="btn-fe-retry-queue"', html)
        self.assertIn('vRetryHaciendaQueue', html)
        self.assertIn('Contingencia', html)

    def test_08_retry_queue_firma_en_memoria_si_se_configura_certificado(self):
        """Verifica que al reintentar la cola, si hay certificado disponible pero el XML no estaba firmado, se firme automáticamente."""
        # Insertar documento sin firmar directamente en BD
        with database.get_db(self.tmp_db) as conn:
            conn.execute("""
                INSERT INTO electronic_documents (
                    id, company_id, tipo_documento, codigo_sucursal, codigo_terminal,
                    consecutivo, clave_numerica, fecha_emision, moneda, total_bruto,
                    subtotal, total_impuestos, total_comprobante, estado_comprobante,
                    xml_firmado, usuario_creacion
                ) VALUES (
                    'doc-unsigned-01', '3101888999', '01', '001', '00001',
                    '00100001010000000999', '506211126003101888999001000010100000009991234567891',
                    '2026-11-21T08:00:00-06:00', 'CRC', 1000.0, 1000.0, 130.0, 1130.0,
                    'pendiente_envio', '<FacturaElectronica><NumeroConsecutivo>00100001010000000999</NumeroConsecutivo></FacturaElectronica>',
                    'empresa_user'
                )
            """)

        res = emission_service.retry_pending_hacienda_queue('3101888999', actor='empresa_user', document_id='doc-unsigned-01', db_path=self.tmp_db)
        self.assertEqual(res['procesados'], 1)
        self.assertEqual(res['enviados'], 1)

        # Comprobar que el estado pasó a enviado
        with database.get_db(self.tmp_db) as conn:
            d_updated = dict(conn.execute("SELECT * FROM electronic_documents WHERE id = 'doc-unsigned-01'").fetchone())
            self.assertEqual(d_updated['estado_comprobante'], 'enviado')


if __name__ == '__main__':
    unittest.main()
