"""
FACTURAMI - Suite de Pruebas de Seguridad y Autorizacion (Fase 1).
==================================================================
Valida la mitigacion de los 5 hallazgos criticos del reporte de seguridad:
1. Autenticacion y control de acceso estricto a rutas admin (/api/admin/*)
2. Proteccion contra IDOR/BOLA en contabilidad (/api/contabilidad/*) y cola (/api/facturas/*)
3. Aislamiento estricto multi-tenant en sincronizacion (/api/sync/pull y /api/sync/push)
4. Sanitizacion de subida de archivos (magic bytes, path traversal, cuotas y webhook_url)
5. Proteccion de endpoints internos de n8n (/api/internal/* y POST /api/facturas/queue)
   con eliminacion total del bypass por IP privada y tokens hardcodeados.
"""
import io
import json
import os
import shutil
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

# ── Configurar entorno aislado antes de importar componentes ────────────────
TEST_TMP = tempfile.mkdtemp(prefix="facturami_test_fase1_")
TEST_DB = os.path.join(TEST_TMP, "test_facturami.db")
TEST_USERS = os.path.join(TEST_TMP, "test_users.json")
TEST_STORAGE = os.path.join(TEST_TMP, "storage")
TEST_USB_JSON = os.path.join(TEST_TMP, "usb_test.json")

os.environ['FACTURAMI_DB_PATH'] = TEST_DB
os.environ['FACTURAMI_USERS_FILE'] = TEST_USERS
os.environ['FACTURAMI_STORAGE_PATH'] = TEST_STORAGE
os.environ['FACTURAMI_USB_JSON'] = TEST_USB_JSON
os.environ['JWT_SECRET_KEY'] = 'test-secret-key-fase1-authz-must-be-at-least-32-chars-long-123456'
os.environ['N8N_INTERNAL_SECRET'] = 'test-n8n-internal-secret-at-least-32-chars-long-abcdef123456789'
os.environ['N8N_WEBHOOK_URL'] = 'http://127.0.0.1:5678/webhook/test-importar'

import db as database
database.DB_PATH = TEST_DB
import services.auth_service as auth_service
auth_service.USERS_FILE = TEST_USERS
import services.sync_service as sync_service
sync_service.USB_JSON = TEST_USB_JSON
from scripts.migrate_v2_niif import migrate

from app import create_app, _require_strong_secrets
from flask_jwt_extended import create_access_token


class TestFase1Seguridad(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        os.makedirs(TEST_STORAGE, exist_ok=True)
        # Inicializar base de datos SQLite limpia
        database.init_db(TEST_DB)
        # Aplicar migraciones NIIF (asientos, columnas de origen_fondos, etc.)
        migrate(TEST_DB)

        # Configurar cuentas de prueba
        with database.get_db(TEST_DB) as conn:
            # Crear tabla cuentas si no existiera
            conn.execute("""
                CREATE TABLE IF NOT EXISTS cuentas (
                    username TEXT PRIMARY KEY,
                    password_hash TEXT NOT NULL,
                    nombre TEXT NOT NULL,
                    email TEXT NOT NULL,
                    role TEXT NOT NULL DEFAULT 'contador',
                    plan_id TEXT NOT NULL DEFAULT 'pro',
                    limite_facturas_mes INTEGER NOT NULL DEFAULT 3000,
                    creditos_extra INTEGER NOT NULL DEFAULT 0,
                    facturas_mes INTEGER NOT NULL DEFAULT 0,
                    activo INTEGER NOT NULL DEFAULT 1,
                    creado_at DATETIME DEFAULT (datetime('now','localtime')),
                    ultimo_login DATETIME
                )
            """)
            # Cuenta Admin
            conn.execute("""
                INSERT OR REPLACE INTO cuentas (username, password_hash, nombre, email, role, activo)
                VALUES ('admin_user', 'hash_admin', 'Administrador', 'admin@facturami.app', 'admin', 1)
            """)
            # Contador 1
            conn.execute("""
                INSERT OR REPLACE INTO cuentas (username, password_hash, nombre, email, role, activo)
                VALUES ('contador_1', 'hash_c1', 'Contador Uno', 'c1@facturami.app', 'contador', 1)
            """)
            # Contador 2
            conn.execute("""
                INSERT OR REPLACE INTO cuentas (username, password_hash, nombre, email, role, activo)
                VALUES ('contador_2', 'hash_c2', 'Contador Dos', 'c2@facturami.app', 'contador', 1)
            """)
            # Contador Suspendido
            conn.execute("""
                INSERT OR REPLACE INTO cuentas (username, password_hash, nombre, email, role, activo)
                VALUES ('contador_suspendido', 'hash_susp', 'Contador Suspendido', 'susp@facturami.app', 'contador', 0)
            """)

            # Clientes para cada contador
            conn.execute("""
                INSERT OR REPLACE INTO clientes (id, nombre, tipo, cuenta_id, email)
                VALUES ('cli_tenant_1', 'Cliente de Contador 1', 'PF', 'contador_1', 'cli1@test.com')
            """)
            conn.execute("""
                INSERT OR REPLACE INTO clientes (id, nombre, tipo, cuenta_id, email)
                VALUES ('cli_tenant_2', 'Cliente de Contador 2', 'PJ', 'contador_2', 'cli2@test.com')
            """)

            # Configuración para cli_tenant_1
            conn.execute("""
                INSERT OR REPLACE INTO clientes_config (
                    cliente_id, razon_social_prompt, email_recepcion,
                    drive_folder_inbox, drive_folder_processed, drive_folder_error, activo
                )
                VALUES (
                    'cli_tenant_1', 'Cliente 1 SA', 'facturas-cli1@facturami.app',
                    'folder_inbox_1', 'folder_proc_1', 'folder_err_1', 1
                )
            """)

            # Factura en cola para cli_tenant_1
            conn.execute("""
                INSERT OR REPLACE INTO facturas_queue (id, cliente_id, estado, factura, total, hash_archivo)
                VALUES ('factura_q_cli1', 'cli_tenant_1', 'pendiente', 'FAC-001', '50000', 'a'*64)
            """)
            # Factura en cola para cli_tenant_2
            conn.execute("""
                INSERT OR REPLACE INTO facturas_queue (id, cliente_id, estado, factura, total, hash_archivo)
                VALUES ('factura_q_cli2', 'cli_tenant_2', 'pendiente', 'FAC-002', '90000', 'b'*64)
            """)

        # Crear archivo users.json para auth_service y admin_bp
        users_payload = [
            {"username": "admin_user", "password_hash": "$2b$12$dummy", "role": "admin", "name": "Admin", "active": True},
            {"username": "contador_1", "password_hash": "$2b$12$dummy", "role": "contador", "name": "C1", "active": True},
            {"username": "contador_2", "password_hash": "$2b$12$dummy", "role": "contador", "name": "C2", "active": True},
            {"username": "contador_suspendido", "password_hash": "$2b$12$dummy", "role": "contador", "name": "Susp", "active": False}
        ]
        with open(TEST_USERS, 'w', encoding='utf-8') as f:
            json.dump(users_payload, f)

        cls.app = create_app()
        cls.client = cls.app.test_client()

        # Generar tokens JWT para pruebas
        with cls.app.app_context():
            cls.token_admin = create_access_token(identity='admin_user', additional_claims={'role': 'admin', 'name': 'Admin'})
            cls.token_c1 = create_access_token(identity='contador_1', additional_claims={'role': 'contador', 'name': 'C1'})
            cls.token_c2 = create_access_token(identity='contador_2', additional_claims={'role': 'contador', 'name': 'C2'})
            cls.token_susp = create_access_token(identity='contador_suspendido', additional_claims={'role': 'contador', 'name': 'Susp'})

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(TEST_TMP, ignore_errors=True)

    def auth_h(self, token):
        return {'Authorization': f'Bearer {token}'}

    # ══════════════════════════════════════════════════════════════════════
    #  1. Secretos y Startup Seguro
    # ══════════════════════════════════════════════════════════════════════

    def test_01_strong_secrets_enforcement(self):
        """Verifica que el sistema se niega a arrancar con secretos débiles o ausentes."""
        with patch.dict(os.environ, {'JWT_SECRET_KEY': 'corta'}):
            with self.assertRaises(RuntimeError):
                _require_strong_secrets()

        with patch.dict(os.environ, {'JWT_SECRET_KEY': 'CAMBIAR_ESTE_TOKEN_SECRET_EN_PROD_123456789'}):
            with self.assertRaises(RuntimeError):
                _require_strong_secrets()

        with patch.dict(os.environ, {'N8N_INTERNAL_SECRET': ''}):
            with self.assertRaises(RuntimeError):
                _require_strong_secrets()

    # ══════════════════════════════════════════════════════════════════════
    #  2. Rutas Admin (/api/admin/*)
    # ══════════════════════════════════════════════════════════════════════

    def test_02_admin_routes_reject_anonymous(self):
        """Peticiones anónimas a endpoints de admin deben responder 401."""
        endpoints = [
            ('/api/admin/contadores', 'GET'),
            ('/api/admin/contadores', 'POST'),
            ('/api/admin/contadores/contador_1', 'PUT'),
            ('/api/admin/contadores/contador_1/extender-suscripcion', 'POST'),
            ('/api/admin/contadores/contador_1/reset-facturas', 'POST'),
            ('/api/admin/contadores/contador_1', 'DELETE')
        ]
        for url, method in endpoints:
            if method == 'GET':
                res = self.client.get(url)
            elif method == 'POST':
                res = self.client.post(url, json={})
            elif method == 'PUT':
                res = self.client.put(url, json={})
            elif method == 'DELETE':
                res = self.client.delete(url)
            self.assertEqual(res.status_code, 401, f"{method} {url} devolvió {res.status_code} sin token (esperado 401)")

    def test_03_admin_routes_reject_regular_accountant(self):
        """Un contador autenticado (no admin) debe recibir 403 Forbidden en rutas admin."""
        res = self.client.get('/api/admin/contadores', headers=self.auth_h(self.token_c1))
        self.assertEqual(res.status_code, 403, "Contador no debe acceder a /api/admin/contadores")

        res = self.client.post('/api/admin/contadores', json={}, headers=self.auth_h(self.token_c1))
        self.assertEqual(res.status_code, 403)

    def test_04_admin_routes_allow_admin(self):
        """Un usuario con rol 'admin' puede acceder a /api/admin/contadores."""
        res = self.client.get('/api/admin/contadores', headers=self.auth_h(self.token_admin))
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data.get('ok'))
        self.assertIsInstance(data.get('data'), list)

    # ══════════════════════════════════════════════════════════════════════
    #  3. Contabilidad NIIF: Control de Acceso y Aislamiento (IDOR)
    # ══════════════════════════════════════════════════════════════════════

    def test_05_contabilidad_rejects_anonymous(self):
        """Endpoints de contabilidad deben responder 401 si no hay token."""
        res = self.client.get('/api/contabilidad/auditoria?cliente_id=cli_tenant_1')
        self.assertEqual(res.status_code, 401, "Auditoría debe requerir token")

        res = self.client.get('/api/contabilidad/asientos?cliente_id=cli_tenant_1')
        self.assertEqual(res.status_code, 401, "Asientos debe requerir token")

        res = self.client.get('/api/contabilidad/balance-general?cliente_id=cli_tenant_1')
        self.assertEqual(res.status_code, 401, "Balance debe requerir token")

        res = self.client.post('/api/contabilidad/ajuste-balance', json={'cliente_id': 'cli_tenant_1'})
        self.assertEqual(res.status_code, 401, "Ajuste balance debe requerir token")

    def test_06_contabilidad_cross_tenant_isolation(self):
        """Contador 1 NO debe poder ver ni modificar contabilidad de cli_tenant_2 (de Contador 2)."""
        # Consulta auditoria
        res = self.client.get('/api/contabilidad/auditoria?cliente_id=cli_tenant_2', headers=self.auth_h(self.token_c1))
        self.assertEqual(res.status_code, 403, "Contador 1 no debe auditar cliente de Contador 2")

        # Consulta asientos
        res = self.client.get('/api/contabilidad/asientos?cliente_id=cli_tenant_2', headers=self.auth_h(self.token_c1))
        self.assertEqual(res.status_code, 403, "Contador 1 no debe ver asientos de cliente de Contador 2")

        # Inserción de asiento ajeno
        asiento_body = {
            'asiento': {'cliente_id': 'cli_tenant_2', 'concepto': 'Intento de inyección cross-tenant'},
            'lineas': [
                {'cuenta_codigo': '1.1.1.01.01', 'cuenta_nombre': 'Caja', 'debito': 1000, 'credito': 0},
                {'cuenta_codigo': '4.1.1.01.01', 'cuenta_nombre': 'Ventas', 'debito': 0, 'credito': 1000}
            ]
        }
        res = self.client.post('/api/contabilidad/asientos', json=asiento_body, headers=self.auth_h(self.token_c1))
        self.assertEqual(res.status_code, 403, "Contador 1 no debe insertar asientos en cliente de Contador 2")

        # Acceso a su propio cliente debe ser exitoso
        res_own = self.client.get('/api/contabilidad/asientos?cliente_id=cli_tenant_1', headers=self.auth_h(self.token_c1))
        self.assertEqual(res_own.status_code, 200, "Contador 1 debe poder consultar sus propios clientes")

    # ══════════════════════════════════════════════════════════════════════
    #  4. Cola de Facturas: Control de Acceso y Visor PDF
    # ══════════════════════════════════════════════════════════════════════

    def test_07_queue_tenant_isolation(self):
        """Cola de facturas debe requerir auth y aislar por tenant."""
        # Anónimo
        res = self.client.get('/api/facturas/queue?client_id=cli_tenant_1')
        self.assertEqual(res.status_code, 401)

        res = self.client.get('/api/facturas/queue/count?client_id=cli_tenant_1')
        self.assertEqual(res.status_code, 401)

        # Contador 1 consulta cliente propio
        res = self.client.get('/api/facturas/queue?client_id=cli_tenant_1', headers=self.auth_h(self.token_c1))
        self.assertEqual(res.status_code, 200)
        items = res.get_json()
        self.assertIsInstance(items, list)
        self.assertTrue(any(f.get('_id') == 'factura_q_cli1' for f in items))

        # Contador 1 intenta consultar cliente ajeno
        res = self.client.get('/api/facturas/queue?client_id=cli_tenant_2', headers=self.auth_h(self.token_c1))
        self.assertEqual(res.status_code, 403)

        res = self.client.get('/api/facturas/queue/count?client_id=cli_tenant_2', headers=self.auth_h(self.token_c1))
        self.assertEqual(res.status_code, 403)

    def test_08_pdf_visor_authorization(self):
        """Visor PDF no debe entregar PDFs de otros clientes ni permitir peticiones anónimas."""
        # Anónimo
        res = self.client.get('/api/facturas/factura_q_cli1/pdf')
        self.assertEqual(res.status_code, 401)

        # Contador 1 intenta ver factura de cli_tenant_2
        res = self.client.get('/api/facturas/factura_q_cli2/pdf', headers=self.auth_h(self.token_c1))
        # 404 para no revelar existencia de IDs ajenos
        self.assertEqual(res.status_code, 404)

    # ══════════════════════════════════════════════════════════════════════
    #  5. Sincronizacion Pull/Push Multi-Tenant
    # ══════════════════════════════════════════════════════════════════════

    def test_09_sync_pull_isolated_by_token(self):
        """sync/pull toma la cuenta estrictamente del token y rechaza anónimos."""
        # Anónimo
        res = self.client.get('/api/sync/pull')
        self.assertEqual(res.status_code, 401)

        # Contador 1 intenta pasar ?cuenta_id=contador_2 en query string
        res = self.client.get('/api/sync/pull?cuenta_id=contador_2', headers=self.auth_h(self.token_c1))
        self.assertEqual(res.status_code, 200)
        body = res.get_json()
        # El servidor ignora cuenta_id de la URL y devuelve la del token
        self.assertEqual(body.get('cuenta_id'), 'contador_1')
        clients = body.get('data', {}).get('f_clients', [])
        client_ids = [c['id'] for c in clients]
        self.assertIn('cli_tenant_1', client_ids)
        self.assertNotIn('cli_tenant_2', client_ids)

    def test_10_sync_push_prevents_cross_tenant_tampering(self):
        """sync/push rechaza peticiones que intenten sobrescribir datos de clientes de otros contadores."""
        # Intento de modificar datos de cli_tenant_2 por contador_1
        malicious_payload = {
            'f_clients': [{'id': 'cli_tenant_2', 'name': 'Hackeado'}],
            'f_data_cli_tenant_2': {
                'ingresos': {'rows': [{'fecha': '2026-01-01', 'tf': 9999999}]}
            }
        }
        res = self.client.post('/api/sync/push', json=malicious_payload, headers=self.auth_h(self.token_c1))
        self.assertEqual(res.status_code, 403, "sync/push debe bloquear escritura en cliente ajeno")

        # Push válido para cliente propio
        valid_payload = {
            'f_clients': [{'id': 'cli_tenant_1', 'name': 'Cliente 1 Actualizado'}],
            'f_data_cli_tenant_1': {
                'ingresos': {'rows': [{'fecha': '2026-06-01', 'tf': 150000}]}
            }
        }
        res = self.client.post('/api/sync/push', json=valid_payload, headers=self.auth_h(self.token_c1))
        self.assertEqual(res.status_code, 200)

    # ══════════════════════════════════════════════════════════════════════
    #  6. Seguridad en Subida de Archivos (Magic bytes, Path Traversal, SSRF)
    # ══════════════════════════════════════════════════════════════════════

    def test_11_upload_security_and_magic_bytes(self):
        """Upload exige autenticación, rechaza path traversal y valida magic bytes binarios."""
        # 1. Anónimo -> 401
        res = self.client.post('/api/facturas/upload')
        self.assertEqual(res.status_code, 401)

        # 2. Path traversal en client_id
        data_traversal = {
            'client_id': '../../etc',
            'files': (io.BytesIO(b'%PDF-1.4 test content'), 'test.pdf')
        }
        res = self.client.post(
            '/api/facturas/upload',
            data=data_traversal,
            content_type='multipart/form-data',
            headers=self.auth_h(self.token_c1)
        )
        self.assertIn(res.status_code, (400, 403))

        # 3. Archivo con extensión .pdf pero contenido no-PDF (falsificación de extensión)
        fake_pdf = {
            'client_id': 'cli_tenant_1',
            'files': (io.BytesIO(b'MZ executable header content not a pdf'), 'malicious.pdf')
        }
        res = self.client.post(
            '/api/facturas/upload',
            data=fake_pdf,
            content_type='multipart/form-data',
            headers=self.auth_h(self.token_c1)
        )
        self.assertIn(res.status_code, (200, 207))
        body = res.get_json()
        self.assertFalse(body.get('ok'))
        self.assertTrue(any('Formato no permitido' in d.get('error', '') for d in body.get('detalle', [])))

        # 4. Archivo PDF con firma válida (%PDF-1.4)
        valid_pdf_content = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF"
        valid_file = {
            'client_id': 'cli_tenant_1',
            'files': (io.BytesIO(valid_pdf_content), 'factura_valida.pdf'),
            'webhook_url': 'http://evil-attacker-site.com/steal' # Debe ser ignorado
        }
        with patch('requests.post') as mock_post:
            mock_post.return_value.status_code = 200
            res = self.client.post(
                '/api/facturas/upload',
                data=valid_file,
                content_type='multipart/form-data',
                headers=self.auth_h(self.token_c1)
            )
            self.assertEqual(res.status_code, 200)
            # Verificar que requests.post fue llamado a la URL interna de n8n, NO a evil-attacker-site.com
            call_url = mock_post.call_args[0][0]
            self.assertEqual(call_url, os.environ['N8N_WEBHOOK_URL'])
            # Verificar que incluye header X-N8N-Token
            call_headers = mock_post.call_args[1].get('headers', {})
            self.assertEqual(call_headers.get('X-N8N-Token'), os.environ['N8N_INTERNAL_SECRET'])

    # ══════════════════════════════════════════════════════════════════════
    #  7. Endpoints Internos n8n y Eliminación del Bypass por IP Privada
    # ══════════════════════════════════════════════════════════════════════

    def test_12_n8n_internal_routes_require_token_and_reject_ip_bypass(self):
        """Endpoints /api/internal/* y POST /api/facturas/queue exigen X-N8N-Token."""
        # 1. Petición sin token -> 403
        res = self.client.get('/api/internal/clientes-config')
        self.assertEqual(res.status_code, 403)

        # 2. Petición simulando IP privada (192.168.1.50) sin token -> DEBE DAR 403 (Sin bypass por IP)
        res_ip = self.client.get(
            '/api/internal/clientes-config',
            environ_base={'REMOTE_ADDR': '192.168.1.50'},
            headers={'X-Forwarded-For': '10.0.0.1'}
        )
        self.assertEqual(res_ip.status_code, 403, "Se eliminó el bypass por IP privada: debe dar 403")

        # 3. Petición con token hardcodeado anterior -> DEBE DAR 403
        res_old = self.client.get(
            '/api/internal/clientes-config',
            headers={'X-N8N-Token': 'n8n-internal-token-secret-change-me'}
        )
        self.assertEqual(res_old.status_code, 403, "Tokens fijos anteriores deben ser rechazados")

        # 4. Petición con X-N8N-Token legítimo -> 200
        res_ok = self.client.get(
            '/api/internal/clientes-config',
            headers={'X-N8N-Token': os.environ['N8N_INTERNAL_SECRET']}
        )
        self.assertEqual(res_ok.status_code, 200)

        # 5. POST /api/facturas/queue desde n8n
        new_factura = {
            'client_id': 'cli_tenant_1',
            'FECHA': '2026-11-08',
            'FACTURA': 'N8N-9999',
            'PROVEEDOR': 'ICE Telecomunicaciones',
            'TOTAL': '45000'
        }
        # Sin token -> 403
        res_q_no = self.client.post('/api/facturas/queue', json=new_factura)
        self.assertEqual(res_q_no.status_code, 403)

        # Con token correcto -> 201
        res_q_ok = self.client.post(
            '/api/facturas/queue',
            json=new_factura,
            headers={'X-N8N-Token': os.environ['N8N_INTERNAL_SECRET']}
        )
        self.assertEqual(res_q_ok.status_code, 201)

    # ══════════════════════════════════════════════════════════════════════
    #  8. Revocación de Acceso para Cuentas Suspendidas
    # ══════════════════════════════════════════════════════════════════════

    def test_13_suspended_account_rejected(self):
        """Tokens emitidos a cuentas marcadas como activo=0 deben ser rechazados inmediatamente."""
        res = self.client.get('/api/sync/pull', headers=self.auth_h(self.token_susp))
        self.assertEqual(res.status_code, 401)
        body = res.get_json()
        self.assertEqual(body.get('error'), 'Cuenta inactiva')

    # ══════════════════════════════════════════════════════════════════════
    #  9. Cierre Total: Toda ruta fuera de allowlist DEBE responder 401
    # ══════════════════════════════════════════════════════════════════════

    def test_14_all_routes_require_auth_or_explicit_allowlist(self):
        """
        Inspecciona dinámicamente app.url_map y comprueba que toda ruta
        fuera de la allowlist explícita (login, estáticos, n8n internal) responde 401 sin token.
        Garantiza que ninguna ruta nueva quede expuesta en el futuro.
        """
        # Allowlist explícita de rutas públicas / exentas
        EXEMPT_PATHS = {
            '/', '/index.html', '/landing', '/inicio', '/app', '/login', '/sistema',
            '/FACTURAMI.html', '/faq', '/preguntas', '/preguntas-frecuentes',
            '/preguntas_frecuentes', '/Preguntas Frecuentes', '/Preguntas%20Frecuentes',
            '/favicon.ico', '/manifest.json', '/icon-192.png', '/icon-512.png',
            '/<path:filename>', '/static/<path:filename>',
            '/api/login',
            '/api/config/public'
        }

        # Rutas protegidas por X-N8N-Token (responden 403 sin token n8n)
        N8N_INTERNAL_PATHS = {
            '/api/internal/cliente-config/<client_id>',
            '/api/internal/clientes-config',
            '/api/internal/resolve-client',
        }

        fallas = []

        for rule in self.app.url_map.iter_rules():
            rule_str = rule.rule
            if rule_str in EXEMPT_PATHS:
                continue

            # Rutas de n8n internas (exigen token n8n -> 403)
            if rule_str in N8N_INTERNAL_PATHS:
                # Probar sin header
                test_url = rule_str.replace('<client_id>', 'cli_tenant_1')
                res = self.client.get(test_url)
                if res.status_code != 403:
                    fallas.append(f"{rule_str} esperaba 403 sin X-N8N-Token, devolvió {res.status_code}")
                continue

            # Endpoints con parámetros dinámicos
            test_url = (rule_str
                        .replace('<factura_id>', 'factura_q_cli1')
                        .replace('<username>', 'contador_1')
                        .replace('<asiento_id>', 'asnt_dummy'))

            for method in rule.methods:
                if method in ('HEAD', 'OPTIONS'):
                    continue

                # POST /api/facturas/queue es el endpoint de webhook de n8n (exige X-N8N-Token -> 403)
                if rule_str == '/api/facturas/queue' and method == 'POST':
                    res = self.client.post(test_url, json={})
                    if res.status_code != 403:
                        fallas.append(f"POST {rule_str} esperaba 403 sin X-N8N-Token, devolvió {res.status_code}")
                    continue

                if method == 'GET':
                    res = self.client.get(test_url)
                elif method == 'POST':
                    res = self.client.post(test_url, json={})
                elif method == 'PUT':
                    res = self.client.put(test_url, json={})
                elif method == 'DELETE':
                    res = self.client.delete(test_url)
                else:
                    continue

                # Toda ruta de usuario SIN token debe responder 401
                if res.status_code != 401:
                    fallas.append(f"{method} {rule_str} respondió {res.status_code} sin token (esperado 401)")

        self.assertEqual(len(fallas), 0, "Rutas no protegidas encontradas:\n" + "\n".join(fallas))

    # ══════════════════════════════════════════════════════════════════════
    #  10. Cuentas que existen solo en users.json
    # ══════════════════════════════════════════════════════════════════════

    def test_15_users_only_in_users_json_can_operate(self):
        """Usuarios existentes SOLO en users.json pueden operar si están activos, y son rechazados si active=False."""
        # Agregar usuario solo en users.json (no en SQLite cuentas)
        with open(TEST_USERS, 'r', encoding='utf-8') as f:
            users_list = json.load(f)

        users_list.append({
            "username": "superadmin_json",
            "password_hash": "$2b$12$dummy",
            "role": "admin",
            "name": "SuperAdmin JSON",
            "active": True
        })
        users_list.append({
            "username": "contador_solo_json",
            "password_hash": "$2b$12$dummy",
            "role": "contador",
            "name": "Contador Solo JSON",
            "active": True
        })
        users_list.append({
            "username": "contador_inactivo_json",
            "password_hash": "$2b$12$dummy",
            "role": "contador",
            "name": "Inactivo JSON",
            "active": False
        })
        with open(TEST_USERS, 'w', encoding='utf-8') as f:
            json.dump(users_list, f)

        with self.app.app_context():
            tok_admin_json = create_access_token(identity='superadmin_json', additional_claims={'role': 'admin'})
            tok_c_json = create_access_token(identity='contador_solo_json', additional_claims={'role': 'contador'})
            tok_inact_json = create_access_token(identity='contador_inactivo_json', additional_claims={'role': 'contador'})

        # Admin solo de users.json puede consultar admin
        res_adm = self.client.get('/api/admin/contadores', headers=self.auth_h(tok_admin_json))
        self.assertEqual(res_adm.status_code, 200, "Admin de users.json debe poder operar")

        # Contador solo de users.json puede hacer sync pull
        res_c = self.client.get('/api/sync/pull', headers=self.auth_h(tok_c_json))
        self.assertEqual(res_c.status_code, 200, "Contador de users.json debe poder operar")
        self.assertEqual(res_c.get_json().get('cuenta_id'), 'contador_solo_json')

        # Contador con active=False en users.json debe ser rechazado
        res_inact = self.client.get('/api/sync/pull', headers=self.auth_h(tok_inact_json))
        self.assertEqual(res_inact.status_code, 401)
        self.assertEqual(res_inact.get_json().get('error'), 'Cuenta inactiva')

    # ══════════════════════════════════════════════════════════════════════
    #  11. sync_push con cliente nuevo
    # ══════════════════════════════════════════════════════════════════════

    def test_16_sync_push_new_client_registers_under_token_account(self):
        """Un cliente nuevo en f_clients y f_data se registra a nombre del contador del token sin colisión."""
        new_cid = 'cli_nuevo_total_999'
        payload = {
            'f_clients': [{'id': new_cid, 'name': 'Empresa Recién Creada S.A.', 'type': 'PJ'}],
            'f_data_cli_nuevo_total_999': {
                'ingresos': {'rows': [{'fecha': '2026-11-01', 'tf': 850000, 'doc': 'F-001'}]}
            }
        }
        res = self.client.post('/api/sync/push', json=payload, headers=self.auth_h(self.token_c1))
        self.assertEqual(res.status_code, 200)

        # Verificar en base de datos que quedó a nombre de contador_1
        with database.get_db(TEST_DB) as conn:
            row = conn.execute("SELECT cuenta_id, nombre FROM clientes WHERE id=?", (new_cid,)).fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row['cuenta_id'], 'contador_1')
            self.assertEqual(row['nombre'], 'Empresa Recién Creada S.A.')

    # ══════════════════════════════════════════════════════════════════════
    #  12. Dos contadores con el mismo cliente_id (Colisión de Cédulas)
    # ══════════════════════════════════════════════════════════════════════

    def test_17_two_accountants_same_client_id_conflict(self):
        """
        Si un cliente (cédula) ya pertenece a Contador 1, Contador 2 NO puede
        registrarlo, sobreescribir sus datos contables ni consultar sus transacciones.
        """
        # cli_tenant_1 pertenece a contador_1. Contador 2 intenta registrarlo como suyo.
        malicious_claim = {
            'f_clients': [{'id': 'cli_tenant_1', 'name': 'Intento de toma de control'}],
            'f_data_cli_tenant_1': {
                'ingresos': {'rows': [{'fecha': '2026-01-01', 'tf': 111111}]}
            }
        }
        res = self.client.post('/api/sync/push', json=malicious_claim, headers=self.auth_h(self.token_c2))
        self.assertEqual(res.status_code, 403, "Contador 2 debe recibir 403 al intentar registrar cliente de Contador 1")

        # Verificar que el cliente NO fue modificado en la BD (sigue perteneciendo a contador_1)
        with database.get_db(TEST_DB) as conn:
            row = conn.execute("SELECT cuenta_id, nombre FROM clientes WHERE id='cli_tenant_1'").fetchone()
            self.assertEqual(row['cuenta_id'], 'contador_1')
            self.assertNotEqual(row['nombre'], 'Intento de toma de control')

        # Contador 2 tampoco puede consultar facturas ni auditoría de ese cliente
        res_aud = self.client.get('/api/contabilidad/auditoria?cliente_id=cli_tenant_1', headers=self.auth_h(self.token_c2))
        self.assertEqual(res_aud.status_code, 403)

        res_q = self.client.get('/api/facturas/queue?client_id=cli_tenant_1', headers=self.auth_h(self.token_c2))
        self.assertEqual(res_q.status_code, 403)

    # ══════════════════════════════════════════════════════════════════════
    #  13. Subida de Fotos Celular: HEIC (iPhone) y WEBP
    # ══════════════════════════════════════════════════════════════════════

    def test_18_upload_heic_and_webp_photos(self):
        """Soporta fotos de iPhone (HEIC contenedor ftypheic) e imágenes WEBP con validación de magic bytes."""
        # 1. WEBP binario válido
        webp_bytes = b"RIFF\x1a\x00\x00\x00WEBPVP8 \x0e\x00\x00\x00\x30\x01\x00\x9d\x01\x2a\x01\x00\x01\x00\x02\x00"
        with patch('requests.post') as mock_post:
            mock_post.return_value.status_code = 200
            res = self.client.post(
                '/api/facturas/upload',
                data={'client_id': 'cli_tenant_1', 'files': (io.BytesIO(webp_bytes), 'foto_factura.webp')},
                content_type='multipart/form-data',
                headers=self.auth_h(self.token_c1)
            )
            self.assertEqual(res.status_code, 200)
            self.assertTrue(res.get_json().get('ok'))
            post_data = mock_post.call_args[1].get('data', {})
            self.assertEqual(post_data.get('origen'), 'foto_manual')

        # 2. iPhone HEIC binario válido (ftypheic)
        heic_bytes = b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00mif1heic" + b"\x00" * 32
        with patch('requests.post') as mock_post:
            mock_post.return_value.status_code = 200
            res = self.client.post(
                '/api/facturas/upload',
                data={'client_id': 'cli_tenant_1', 'files': (io.BytesIO(heic_bytes), 'IMG_4021.HEIC')},
                content_type='multipart/form-data',
                headers=self.auth_h(self.token_c1)
            )
            self.assertEqual(res.status_code, 200)
            self.assertTrue(res.get_json().get('ok'))
            post_data = mock_post.call_args[1].get('data', {})
            self.assertEqual(post_data.get('origen'), 'foto_manual')

        # 3. Archivo con nombre .heic pero contenido falso (ejecutable o texto)
        fake_heic = b"NO SOY UNA FOTO HEIC DE IPHONE NI TENGO MAGIC BYTES"
        res_fake = self.client.post(
            '/api/facturas/upload',
            data={'client_id': 'cli_tenant_1', 'files': (io.BytesIO(fake_heic), 'fake.heic')},
            content_type='multipart/form-data',
            headers=self.auth_h(self.token_c1)
        )
        self.assertIn(res_fake.status_code, (200, 207))
        body = res_fake.get_json()
        self.assertFalse(body.get('ok'))
        self.assertTrue(any('Formato no permitido' in d.get('error', '') for d in body.get('detalle', [])))


if __name__ == '__main__':
    unittest.main()
