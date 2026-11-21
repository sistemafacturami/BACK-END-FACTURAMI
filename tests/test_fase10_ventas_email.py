"""
FACTURAMI - Pruebas Unitarias y de Integración Fase 10: Envío de Comprobantes por Correo SMTP.
==============================================================================================
Valida:
  1. Configuración de parámetros SMTP por empresa con cifrado AES-GCM de contraseñas.
  2. Sanitización estricta de credenciales SMTP en el contexto comercial (nunca expuestas).
  3. Prueba de conexión SMTP mediante el servicio y los endpoints de la API.
  4. Generación de cuerpos MIME y plantillas HTML oficiales con desglose de factura y clave 50 dígitos.
  5. Despacho de comprobante con adjuntos (PDF oficial y XML firmado XAdES-BES).
  6. Actualización de trazabilidad de correo en electronic_documents y audit_log.
  7. Despacho automático durante la emisión cuando el cliente receptor posee correo electrónico.
  8. Endpoint de reenvío manual POST /api/ventas/documents/<id>/send-email.
"""
import os
import tempfile
import unittest

os.environ.setdefault('JWT_SECRET_KEY', 'test-jwt-secret-key-0123456789-abcdef')
os.environ.setdefault('N8N_INTERNAL_SECRET', 'test-n8n-secret-key-0123456789-abcdef')
os.environ['FACTURAMI_FE_SECRET_KEY'] = 'test-fe-secret-key-0123456789-abcdefghij'
os.environ['FACTURAMI_FE_SIMULATE_HACIENDA'] = '1'
os.environ['FACTURAMI_MAIL_SIMULATE'] = '1'

from flask_jwt_extended import create_access_token
import db as database
from app import create_app
import services.secret_store as secret_store
import services.ventas_service as ventas_service
import services.ventas_config_service as cfg_service
import services.fe_emission_service as emission_service
import services.fe_mail_service as mail_service


class TestFase10VentasEmail(unittest.TestCase):
    def setUp(self):
        fd, self.tmp_db = tempfile.mkstemp(suffix='.db')
        os.close(fd)
        self.prev_db_path = database.DB_PATH
        database.DB_PATH = self.tmp_db
        database.init_db(self.tmp_db)
        ventas_service.init_ventas_schema(self.tmp_db)
        self.app = create_app()
        self.c = self.app.test_client()

        with database.get_db(self.tmp_db) as conn:
            database.upsert_cuenta(conn, {
                'username': 'empresa_mail_user',
                'nombre': 'Titular Email',
                'role': 'contador',
                'activo': 1
            })
            database.upsert_cliente(conn, {
                'id': '3101777666',
                'type': 'PJ',
                'name': 'Servicios Digitales Alajuela S.A.',
                'email': 'facturacion@digitales.cr'
            }, cuenta_id='empresa_mail_user')

        with self.app.app_context():
            self.token = create_access_token(identity='empresa_mail_user')
            self.headers = {'Authorization': f'Bearer {self.token}'}

        ventas_service.get_or_init_company_context('3101777666', db_path=self.tmp_db)

    def tearDown(self):
        database.DB_PATH = self.prev_db_path
        if os.path.exists(self.tmp_db):
            try:
                os.remove(self.tmp_db)
            except OSError:
                pass

    def test_01_configuracion_smtp_cifrada_y_sanitizacion(self):
        """Verifica que la contraseña SMTP se guarde cifrada y nunca se exponga al frontend."""
        # 1. Guardar configuración SMTP
        cambios = cfg_service.update_smtp_settings('3101777666', {
            'smtp_host': 'smtp.gmail.com',
            'smtp_port': 587,
            'smtp_user': 'facturas@digitales.cr',
            'smtp_password': 'mi-super-clave-secreta',
            'smtp_from_name': 'Facturación Servicios Digitales',
            'smtp_tls': 1,
            'smtp_enabled': 1
        }, actor='empresa_mail_user')

        self.assertIn('smtp_host', cambios)
        self.assertIn('smtp_password', cambios)

        # 2. Verificar que en BD esté cifrado
        with database.get_db(self.tmp_db) as conn:
            row = conn.execute("SELECT * FROM company_fe_settings WHERE company_id = '3101777666'").fetchone()
            self.assertNotEqual(row['smtp_password_enc'], 'mi-super-clave-secreta')
            decrypted = secret_store.decrypt('3101777666', row['smtp_password_enc']).decode('utf-8')
            self.assertEqual(decrypted, 'mi-super-clave-secreta')

        # 3. Verificar contexto comercial devuelto al frontend (no debe tener contraseña en plano)
        ctx = ventas_service.get_or_init_company_context('3101777666', db_path=self.tmp_db)
        settings = ctx['settings']
        self.assertNotIn('smtp_password_enc', settings)
        self.assertTrue(settings.get('has_smtp_password'))
        self.assertEqual(settings.get('smtp_host'), 'smtp.gmail.com')
        self.assertEqual(settings.get('smtp_enabled'), 1)

    def test_02_prueba_de_conexion_smtp_servicio_y_endpoint(self):
        """Verifica el endpoint de prueba de conexión SMTP."""
        # 1. Prueba directa del servicio en simulación
        res_serv = mail_service.test_smtp_connection({
            'host': 'smtp.test.com',
            'port': 587,
            'simulated': True
        })
        self.assertTrue(res_serv['ok'])

        # 2. Prueba vía endpoint de la API
        res = self.c.post('/api/ventas/config/test-smtp', json={
            'company_id': '3101777666',
            'smtp_host': 'smtp.gmail.com',
            'smtp_port': 587,
            'smtp_user': 'test@digitales.cr',
            'smtp_password': 'dummy-password'
        }, headers=self.headers)

        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data['ok'])
        self.assertIn('Conexión SMTP exitosa', data['message'])

    def test_03_construccion_contenido_correo_y_plantilla_html(self):
        """Verifica la generación de texto plano y HTML con datos fiscales."""
        doc = {
            'tipo_documento': '01',
            'consecutivo': '00100001010000000045',
            'clave_numerica': '50621112600310177766600100001010000000045123456789',
            'customer_nombre': 'Cliente VIP Costa Rica',
            'moneda': 'CRC',
            'subtotal': 10000.0,
            'total_impuestos': 1300.0,
            'total_comprobante': 11300.0,
            'fecha_emision': '2026-11-21T09:00:00-06:00'
        }
        emisor = {
            'nombre_comercial': 'Servicios Digitales Alajuela S.A.',
            'identificacion': '3101777666'
        }

        asunto, txt, html = mail_service.build_document_email_content(doc, emisor)

        self.assertIn('00100001010000000045', asunto)
        self.assertIn('Servicios Digitales', asunto)
        self.assertIn('11 300.00', txt)
        self.assertIn('50621112600310177766600100001010000000045123456789', html)
        self.assertIn('Cliente VIP Costa Rica', html)
        self.assertIn('Ministerio de Hacienda CR', html)

    def test_04_despacho_correo_con_adjuntos_pdf_y_xml(self):
        """Verifica el envío de correo de un documento existente y actualización de trazabilidad."""
        # Configurar SMTP activo
        cfg_service.update_smtp_settings('3101777666', {
            'smtp_host': 'smtp.servidor.cr',
            'smtp_port': 587,
            'smtp_user': 'envios@digitales.cr',
            'smtp_password': 'password123',
            'smtp_enabled': 1
        }, actor='empresa_mail_user')

        # Emitir documento
        doc = emission_service.emitir_comprobante('3101777666', {
            'tipo_documento': '01',
            'customer_nombre': 'Cliente Pruebas Correo',
            'customer_email': 'cliente@correo.cr',
            'lineas': [{'descripcion': 'Desarrollo de Software', 'cantidad': 1, 'precio_unitario': 25000, 'codigo_cabys': '1234567890123'}]
        }, actor='empresa_mail_user', db_path=self.tmp_db)

        # Despachar correo explícitamente
        res_mail = mail_service.send_document_email('3101777666', doc['id'], db_path=self.tmp_db)
        self.assertTrue(res_mail['ok'])
        self.assertEqual(res_mail['destinatario'], 'cliente@correo.cr')
        self.assertEqual(len(res_mail['adjuntos']), 2)

        # Comprobar estado en la base de datos
        with database.get_db(self.tmp_db) as conn:
            d_row = dict(conn.execute("SELECT * FROM electronic_documents WHERE id = ?", (doc['id'],)).fetchone())
            self.assertEqual(d_row['correo_estado'], 'enviado')
            self.assertEqual(d_row['correo_destinatario'], 'cliente@correo.cr')
            self.assertTrue(bool(d_row['correo_enviado_at']))

    def test_05_despacho_automatico_en_emision_cuando_hay_correo_receptor(self):
        """Verifica que emitir_comprobante dispare automáticamente el correo si el cliente tiene email y SMTP está activo."""
        cfg_service.update_smtp_settings('3101777666', {
            'smtp_host': 'smtp.servidor.cr',
            'smtp_port': 587,
            'smtp_user': 'auto@digitales.cr',
            'smtp_password': 'password123',
            'smtp_enabled': 1
        }, actor='empresa_mail_user')

        doc = emission_service.emitir_comprobante('3101777666', {
            'tipo_documento': '01',
            'customer_nombre': 'Auto Correo SA',
            'customer_email': 'receptor.auto@empresa.cr',
            'lineas': [{'descripcion': 'Consultoría Automatizada', 'cantidad': 1, 'precio_unitario': 50000, 'codigo_cabys': '1234567890123'}]
        }, actor='empresa_mail_user', db_path=self.tmp_db)

        self.assertEqual(doc.get('correo_estado'), 'enviado')
        self.assertEqual(doc.get('correo_destinatario'), 'receptor.auto@empresa.cr')

    def test_06_endpoint_reenvio_correo_a_otro_destinatario(self):
        """Verifica POST /api/ventas/documents/<id>/send-email permitiendo cambiar destinatario."""
        cfg_service.update_smtp_settings('3101777666', {
            'smtp_host': 'smtp.servidor.cr',
            'smtp_port': 587,
            'smtp_user': 'envios@digitales.cr',
            'smtp_password': 'password123',
            'smtp_enabled': 1
        }, actor='empresa_mail_user')

        doc = emission_service.emitir_comprobante('3101777666', {
            'tipo_documento': '01',
            'customer_nombre': 'Reenvio Test',
            'customer_email': 'original@correo.cr',
            'lineas': [{'descripcion': 'Licencia Anual', 'cantidad': 1, 'precio_unitario': 100000, 'codigo_cabys': '1234567890123'}]
        }, actor='empresa_mail_user', db_path=self.tmp_db)

        # Reenviar a un correo alternativo
        res = self.c.post(f"/api/ventas/documents/{doc['id']}/send-email", json={
            'company_id': '3101777666',
            'email': 'contabilidad.externa@empresa.cr'
        }, headers=self.headers)

        self.assertEqual(res.status_code, 200)
        data = res.get_json()['data']
        self.assertTrue(data['ok'])
        self.assertEqual(data['destinatario'], 'contabilidad.externa@empresa.cr')

    def test_07_elementos_de_interfaz_smtp_en_facturami_html(self):
        """Verifica que FACTURAMI.html cuente con los controles SMTP y el botón de correo en documentos."""
        html_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'FACTURAMI.html')
        self.assertTrue(os.path.exists(html_path))
        with open(html_path, 'r', encoding='utf-8') as f:
            html = f.read()

        self.assertIn('id="cfg_smtp_host"', html)
        self.assertIn('id="cfg_smtp_port"', html)
        self.assertIn('id="cfg_smtp_user"', html)
        self.assertIn('id="cfg_smtp_pass"', html)
        self.assertIn('vSaveSmtp', html)
        self.assertIn('vTestSmtp', html)
        self.assertIn('vSendEmailDoc', html)
        self.assertIn('✉️ Correo', html)


if __name__ == '__main__':
    unittest.main()
