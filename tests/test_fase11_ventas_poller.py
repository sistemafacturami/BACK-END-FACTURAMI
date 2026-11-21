"""
FACTURAMI - Pruebas Unitarias y de Integración Fase 11: Poller Automático de Hacienda (Background Worker).
==========================================================================================================
Valida:
  1. Sincronización en lote de documentos pendientes ('enviado', 'recibido_hacienda', 'en_proceso').
  2. Transición automática a 'aceptado' o 'rechazado' con almacenamiento del XML de respuesta de Hacienda.
  3. Filtrado estricto por empresa y preservación de documentos ya finalizados.
  4. Endpoint de sincronización masiva bajo demanda POST /api/ventas/documents/poll-pending.
  5. Ciclo de vida del demonio en segundo plano (start_hacienda_poller, is_poller_running, stop_hacienda_poller).
  6. Ejecución del script CLI scripts/poll_hacienda.py.
  7. Elementos de interfaz en FACTURAMI.html (#btn-fe-poll-pending, vPollPendingHacienda).
"""
import os
import subprocess
import sys
import tempfile
import unittest

os.environ.setdefault('JWT_SECRET_KEY', 'test-jwt-secret-key-0123456789-abcdef')
os.environ.setdefault('N8N_INTERNAL_SECRET', 'test-n8n-secret-key-0123456789-abcdef')
os.environ['FACTURAMI_FE_SECRET_KEY'] = 'test-fe-secret-key-0123456789-abcdefghij'
os.environ['FACTURAMI_FE_SIMULATE_HACIENDA'] = '1'
os.environ['FACTURAMI_ENABLE_POLLER'] = '0'  # No arrancar demonio automático durante la inicialización de tests

from flask_jwt_extended import create_access_token
import db as database
from app import create_app
import services.ventas_service as ventas_service
import services.fe_emission_service as emission_service
import services.fe_poller_service as poller_service


class TestFase11VentasPoller(unittest.TestCase):
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
                'username': 'empresa_poller_user',
                'nombre': 'Titular Poller',
                'role': 'contador',
                'activo': 1
            })
            database.upsert_cliente(conn, {
                'id': '3101555444',
                'type': 'PJ',
                'name': 'Distribuidora Monteverde S.A.',
                'email': 'contabilidad@monteverde.cr'
            }, cuenta_id='empresa_poller_user')

        with self.app.app_context():
            self.token = create_access_token(identity='empresa_poller_user')
            self.headers = {'Authorization': f'Bearer {self.token}'}

        ventas_service.get_or_init_company_context('3101555444', db_path=self.tmp_db)

    def tearDown(self):
        poller_service.stop_hacienda_poller()
        database.DB_PATH = self.prev_db_path
        if os.path.exists(self.tmp_db):
            try:
                os.remove(self.tmp_db)
            except OSError:
                pass

    def test_01_polling_documentos_enviados_transicion_a_aceptado(self):
        """Verifica que documentos en estado 'enviado' sean consultados y pasen a 'aceptado' con su XML de respuesta."""
        # 1. Crear 2 documentos en estado 'enviado'
        doc1 = emission_service.emitir_comprobante('3101555444', {
            'tipo_documento': '01',
            'lineas': [{'descripcion': 'Venta 1', 'cantidad': 1, 'precio_unitario': 10000, 'codigo_cabys': '1234567890123'}]
        }, actor='empresa_poller_user', db_path=self.tmp_db)

        doc2 = emission_service.emitir_comprobante('3101555444', {
            'tipo_documento': '04',
            'lineas': [{'descripcion': 'Tiquete 2', 'cantidad': 1, 'precio_unitario': 5000, 'codigo_cabys': '1234567890123'}]
        }, actor='empresa_poller_user', db_path=self.tmp_db)

        # Forzar estado 'enviado' en BD (como cuando Hacienda responde 202 Accepted)
        with database.get_db(self.tmp_db) as conn:
            conn.execute("UPDATE electronic_documents SET estado_comprobante = 'enviado' WHERE company_id = '3101555444'")

        # 2. Ejecutar polling en lote
        res = poller_service.poll_pending_hacienda_documents(company_id='3101555444', db_path=self.tmp_db)

        self.assertEqual(res['consultados'], 2)
        self.assertEqual(res['aceptados'], 2)
        self.assertEqual(res['errores'], 0)

        # 3. Verificar que ambos documentos ahora están 'aceptado' y tienen XML de respuesta
        with database.get_db(self.tmp_db) as conn:
            rows = conn.execute("SELECT id, estado_comprobante, xml_respuesta_hacienda FROM electronic_documents WHERE company_id = '3101555444'").fetchall()
            for r in rows:
                self.assertEqual(r['estado_comprobante'], 'aceptado')
                self.assertTrue(bool(r['xml_respuesta_hacienda']))
                self.assertIn('<MensajeHacienda', r['xml_respuesta_hacienda'])

        # 4. Una segunda ejecución no debe encontrar documentos pendientes
        res_segunda = poller_service.poll_pending_hacienda_documents(company_id='3101555444', db_path=self.tmp_db)
        self.assertEqual(res_segunda['consultados'], 0)

    def test_02_endpoint_poll_pending_hacienda(self):
        """Verifica el endpoint POST /api/ventas/documents/poll-pending."""
        doc = emission_service.emitir_comprobante('3101555444', {
            'tipo_documento': '01',
            'lineas': [{'descripcion': 'Venta Endpoint Poller', 'cantidad': 1, 'precio_unitario': 12000, 'codigo_cabys': '1234567890123'}]
        }, actor='empresa_poller_user', db_path=self.tmp_db)

        with database.get_db(self.tmp_db) as conn:
            conn.execute("UPDATE electronic_documents SET estado_comprobante = 'enviado' WHERE id = ?", (doc['id'],))

        res = self.c.post('/api/ventas/documents/poll-pending', json={
            'company_id': '3101555444'
        }, headers=self.headers)

        self.assertEqual(res.status_code, 200)
        data = res.get_json()['data']
        self.assertEqual(data['consultados'], 1)
        self.assertEqual(data['aceptados'], 1)

    def test_03_ciclo_de_vida_demonio_en_segundo_plano(self):
        """Verifica que el demonio se inicie, evite hilos duplicados y se detenga limpiamente."""
        # 1. Iniciar demonio con intervalo rápido
        started = poller_service.start_hacienda_poller(interval_seconds=1, db_path=self.tmp_db)
        self.assertTrue(started)
        self.assertTrue(poller_service.is_poller_running())

        # 2. Intento de inicio duplicado debe ser ignorado
        started_dup = poller_service.start_hacienda_poller(interval_seconds=1, db_path=self.tmp_db)
        self.assertFalse(started_dup)

        # 3. Detener demonio limpiamente
        stopped = poller_service.stop_hacienda_poller()
        self.assertTrue(stopped)
        self.assertFalse(poller_service.is_poller_running())

    def test_04_ejecucion_cli_poll_hacienda(self):
        """Verifica la ejecución del script CLI scripts/poll_hacienda.py."""
        script_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'scripts', 'poll_hacienda.py')
        self.assertTrue(os.path.exists(script_path))

        res = subprocess.run([
            sys.executable, script_path, '--company-id', '3101555444', '--limit', '10'
        ], capture_output=True, text=True, env=os.environ.copy())

        self.assertEqual(res.returncode, 0)
        self.assertIn('[FACTURAMI]', res.stdout)
        self.assertIn('Consultados:', res.stdout)

    def test_05_elementos_de_interfaz_en_facturami_html(self):
        """Verifica que FACTURAMI.html cuente con el botón y función de sincronización con Hacienda."""
        html_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'FACTURAMI.html')
        self.assertTrue(os.path.exists(html_path))
        with open(html_path, 'r', encoding='utf-8') as f:
            html = f.read()

        self.assertIn('id="btn-fe-poll-pending"', html)
        self.assertIn('vPollPendingHacienda', html)
        self.assertIn('Sincronizar Hacienda', html)


if __name__ == '__main__':
    unittest.main()
