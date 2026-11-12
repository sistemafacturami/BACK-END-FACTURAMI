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
        try:
            with database.get_db(TEST_DB) as conn:
                conn.execute("DELETE FROM audit_log")
                conn.execute("DELETE FROM uploads_log")
        except Exception:
            pass

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
            '/<filename>', '/static/<path:filename>',
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

    # ══════════════════════════════════════════════════════════════════════
    #  14. Bloqueante 1: Storage NUNCA es servido por catch-all estático
    # ══════════════════════════════════════════════════════════════════════

    def test_19_storage_no_servido_por_estaticos(self):
        """
        Verifica que ninguna subcarpeta de /storage/ sea accesible de forma anónima
        o mediante el despachador de archivos estáticos. Debe responder 404.
        """
        # Crear un archivo de prueba en el storage del cliente
        cli_dir = os.path.join(TEST_STORAGE, 'cli_tenant_1')
        os.makedirs(cli_dir, exist_ok=True)
        dummy_hash = 'd' * 64
        with open(os.path.join(cli_dir, f"{dummy_hash}.jpg"), 'wb') as f:
            f.write(b'\xff\xd8\xff\xe0' + b'\x00' * 50)

        # 1. Petición anónima directa a /storage/<cid>/<hash>.jpg
        res = self.client.get(f'/storage/cli_tenant_1/{dummy_hash}.jpg')
        self.assertEqual(res.status_code, 404, "El storage no debe ser accesible como ruta estática")

        # 2. Petición con path traversal simulado
        res_trav = self.client.get(f'/storage/../storage/cli_tenant_1/{dummy_hash}.jpg')
        self.assertEqual(res_trav.status_code, 404)

        # 3. Petición anónima a un PDF cualquiera en storage
        res_pdf = self.client.get('/storage/cli_tenant_1/factura.pdf')
        self.assertEqual(res_pdf.status_code, 404)

    # ══════════════════════════════════════════════════════════════════════
    #  15. Bloqueante 2: IDOR por Objeto (factura_id, asiento_id, pasivo_id)
    # ══════════════════════════════════════════════════════════════════════

    def test_20_idor_por_objeto_factura_y_asiento(self):
        """
        Verifica que un usuario no pueda actuar sobre objetos ajenos (facturas, asientos)
        incluso si envía su propio cliente_id o no envía client_id en la petición.
        El objeto debe ser protegido por derivación de propiedad desde la base de datos.
        """
        # Crear un asiento contable legítimo para cli_tenant_2 (de contador_2)
        with database.get_db(TEST_DB) as conn:
            asnt_body = {
                'id': 'asnt_privado_c2',
                'cliente_id': 'cli_tenant_2',
                'concepto': 'Venta legítima de Contador 2'
            }
            lineas = [
                {'cuenta_codigo': '1.1.1.02.01', 'cuenta_nombre': 'Banco', 'debito': 50000, 'credito': 0},
                {'cuenta_codigo': '4.1.1.01.01', 'cuenta_nombre': 'Ventas', 'debito': 0, 'credito': 50000}
            ]
            database.insert_asiento(conn, asnt_body, lineas)

        # 1. Contador 1 intenta DELETE a una factura de Contador 2 (/api/facturas/queue/<id>)
        res_del = self.client.delete('/api/facturas/queue/factura_q_cli2', headers=self.auth_h(self.token_c1))
        # 404 para no revelar existencia
        self.assertEqual(res_del.status_code, 404)
        # Verificar que la factura SIGUE en la base de datos
        with database.get_db(TEST_DB) as conn:
            fq = conn.execute("SELECT estado FROM facturas_queue WHERE id='factura_q_cli2'").fetchone()
            self.assertEqual(fq['estado'], 'pendiente', "La factura de Contador 2 no debió ser afectada")

        # 2. Contador 1 intenta descargar el PDF de la factura de Contador 2
        res_pdf = self.client.get('/api/facturas/factura_q_cli2/pdf', headers=self.auth_h(self.token_c1))
        self.assertEqual(res_pdf.status_code, 404)

        # 3. Contador 1 intenta reversar el asiento de Contador 2 (/api/contabilidad/asientos/<id>/reversar)
        res_rev = self.client.post('/api/contabilidad/asientos/asnt_privado_c2/reversar', json={}, headers=self.auth_h(self.token_c1))
        self.assertEqual(res_rev.status_code, 404)
        # Verificar que el asiento sigue en estado 'valido'
        with database.get_db(TEST_DB) as conn:
            asnt_row = conn.execute("SELECT estado FROM asientos WHERE id='asnt_privado_c2'").fetchone()
            self.assertEqual(asnt_row['estado'], 'valido', "El asiento de Contador 2 no debió ser reversado")

        # 4. Contador 1 intenta anular_pago_pasivo usando su propio cliente_id pero inyectando el asiento ajeno
        malicious_pasivo_payload = {
            'cliente_id': 'cli_tenant_1',
            'pasivo': {
                'id': 'pas_dummy',
                'historial_pagos': [
                    {'pago_id': 'pago_evil_1', 'asiento_id': 'asnt_privado_c2', 'estado': 'aplicado'}
                ]
            },
            'pago_id': 'pago_evil_1'
        }
        res_anular = self.client.post('/api/contabilidad/pasivos/anular-pago', json=malicious_pasivo_payload, headers=self.auth_h(self.token_c1))
        self.assertEqual(res_anular.status_code, 403, "Debe rechazar anular pago con asiento perteneciente a otro cliente")

        # Verificar nuevamente que el asiento de Contador 2 sigue intacto
        with database.get_db(TEST_DB) as conn:
            asnt_row = conn.execute("SELECT estado FROM asientos WHERE id='asnt_privado_c2'").fetchone()
            self.assertEqual(asnt_row['estado'], 'valido')

    # ══════════════════════════════════════════════════════════════════════
    #  16. Bloqueante: sync_push con doble prefijo ('f_data_f_data_<cid>')
    # ══════════════════════════════════════════════════════════════════════

    def test_21_sync_push_clave_f_data_doble_prefijo(self):
        """
        Verifica que una clave manipulada 'f_data_f_data_<cid>' sea rechazada
        y no permita sobrescribir los datos de un cliente ajeno.
        """
        with database.get_db(TEST_DB) as conn:
            conn.execute("""
                INSERT INTO transacciones (id, cliente_id, modulo, fecha, descripcion, total_factura)
                VALUES ('tx_legitima_c1', 'cli_tenant_1', 'ingresos', '2026-10-15', 'Venta Original C1', 75000)
            """)

        payload = {
            'f_data_f_data_cli_tenant_1': {
                'ingresos': {'rows': [{'fecha': '2026-10-30', 'l': 'Ataque Inyectado', 'tf': 1}]}
            }
        }
        res = self.client.post('/api/sync/push', json=payload, headers=self.auth_h(self.token_c2))
        self.assertIn(res.status_code, (400, 403), "Debe rechazar la clave manipulada")

        # Verificar que los datos de cli_tenant_1 siguen intactos
        with database.get_db(TEST_DB) as conn:
            txs = conn.execute("SELECT descripcion, total_factura FROM transacciones WHERE cliente_id='cli_tenant_1'").fetchall()
            self.assertTrue(any(t['descripcion'] == 'Venta Original C1' for t in txs))
            self.assertFalse(any(t['descripcion'] == 'Ataque Inyectado' for t in txs))

    # ══════════════════════════════════════════════════════════════════════
    #  17. Condición de carrera en sync_push con cédula nueva
    # ══════════════════════════════════════════════════════════════════════

    def test_22_sync_push_registro_cedula_y_bloqueo(self):
        """
        Verifica que sync_push valide pertenencia y evite secuestro de cédula nueva.
        Si Contador 1 registra 'new_cli_999', Contador 2 no puede sobrescribirla ni inyectarle f_data.
        """
        p1 = {
            'f_clients': [{'id': 'new_cli_999', 'name': 'Empresa Nueva 999', 'type': 'PJ'}],
            'f_data_new_cli_999': {'ingresos': {'rows': [{'fecha': '2026-10-01', 'l': 'Servicio 1', 'tf': 1000}]}}
        }
        r1 = self.client.post('/api/sync/push', json=p1, headers=self.auth_h(self.token_c1))
        self.assertEqual(r1.status_code, 200)

        # Contador 2 intenta registrar o escribir sobre la misma cédula
        p2 = {
            'f_clients': [{'id': 'new_cli_999', 'name': 'Secuestro de Cédula', 'type': 'PJ'}],
            'f_data_new_cli_999': {'ingresos': {'rows': [{'fecha': '2026-10-01', 'l': 'Venta Falsa', 'tf': 999999}]}}
        }
        r2 = self.client.post('/api/sync/push', json=p2, headers=self.auth_h(self.token_c2))
        self.assertEqual(r2.status_code, 403, "Contador 2 debe recibir 403 al intentar escribir sobre cliente de Contador 1")

        # Verificar que el dueño sigue siendo contador_1
        with database.get_db(TEST_DB) as conn:
            cli = conn.execute("SELECT cuenta_id, nombre FROM clientes WHERE id='new_cli_999'").fetchone()
            self.assertEqual(cli['cuenta_id'], 'contador_1')
            self.assertEqual(cli['nombre'], 'Empresa Nueva 999')

    # ══════════════════════════════════════════════════════════════════════
    #  18. Protección de cuentas bancarias contra secuestro por ON CONFLICT
    # ══════════════════════════════════════════════════════════════════════

    def test_23_cuentas_bancarias_secuestro_prevention(self):
        """
        Verifica que enviar un id de cuenta bancaria perteneciente a otro cliente
        no sobrescriba ni reasigne la cuenta bancaria de la víctima.
        """
        with database.get_db(TEST_DB) as conn:
            conn.execute("""
                INSERT OR REPLACE INTO cuentas_bancarias
                (id, cliente_id, banco_nombre, numero_cuenta, saldo_inicial, activo)
                VALUES ('bank_acc_c1', 'cli_tenant_1', 'BAC San Jose', 'CR1111111111', 100000, 1)
            """)

        # Contador 2 intenta registrar en cli_tenant_2 una cuenta bancaria con el mismo ID 'bank_acc_c1'
        p2 = {
            'f_data_cli_tenant_2': {
                'cuentas_bancarias': [
                    {'id': 'bank_acc_c1', 'banco_nombre': 'Banco Hackeado', 'numero_cuenta': 'CR9999999999', 'saldo_inicial': 500}
                ]
            }
        }
        r2 = self.client.post('/api/sync/push', json=p2, headers=self.auth_h(self.token_c2))
        self.assertEqual(r2.status_code, 200)

        # Verificar que bank_acc_c1 sigue perteneciendo a cli_tenant_1 y con su nombre original
        with database.get_db(TEST_DB) as conn:
            b = conn.execute("SELECT cliente_id, banco_nombre, numero_cuenta FROM cuentas_bancarias WHERE id='bank_acc_c1'").fetchone()
            self.assertEqual(b['cliente_id'], 'cli_tenant_1')
            self.assertEqual(b['banco_nombre'], 'BAC San Jose')

    # ══════════════════════════════════════════════════════════════════════
    #  19. Visor Drive: JSON {"ok": true, "drive_url": ...} sin redirect 302
    # ══════════════════════════════════════════════════════════════════════

    def test_24_visor_drive_json_response(self):
        """
        Cuando una factura solo existe en Google Drive, el backend devuelve 200 con JSON drive_url
        para evitar problemas de CORS y despojo de Bearer tokens al seguir redirecciones.
        """
        with database.get_db(TEST_DB) as conn:
            conn.execute("""
                INSERT OR REPLACE INTO facturas_queue (id, cliente_id, estado, factura, drive_id)
                VALUES ('factura_drive_only', 'cli_tenant_1', 'pendiente', 'FAC-DRIVE-99', '1a2b3c4d5e6f7g8h9i0j')
            """)

        res = self.client.get('/api/facturas/factura_drive_only/pdf', headers=self.auth_h(self.token_c1))
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.is_json)
        data = res.get_json()
        self.assertTrue(data.get('ok'))
        self.assertIn('drive.google.com', data.get('drive_url', ''))

    # ══════════════════════════════════════════════════════════════════════
    #  20. Validación de clientes_config y topes en subidas
    # ══════════════════════════════════════════════════════════════════════

    def test_25_upload_clientes_config_and_limits(self):
        """
        Verifica que upload falle con error claro si falta clientes_config o alguna de sus 3 claves,
        y rechace peticiones con más de 10 archivos.
        """
        with database.get_db(TEST_DB) as conn:
            conn.execute("INSERT OR REPLACE INTO clientes (id, nombre, cuenta_id) VALUES ('cli_sin_cfg', 'Sin Cfg', 'contador_1')")

        pdf_bytes = b"%PDF-1.4\n%test\n%%EOF"
        # 1. Cliente sin clientes_config
        res_nocfg = self.client.post(
            '/api/facturas/upload',
            data={'client_id': 'cli_sin_cfg', 'files': (io.BytesIO(pdf_bytes), 'f.pdf')},
            content_type='multipart/form-data',
            headers=self.auth_h(self.token_c1)
        )
        self.assertEqual(res_nocfg.status_code, 400)
        self.assertIn('clientes_config', res_nocfg.get_json().get('error', ''))

        # 2. Cliente con clientes_config incompleto (falta drive_folder_processed)
        with database.get_db(TEST_DB) as conn:
            conn.execute("INSERT OR REPLACE INTO clientes (id, nombre, cuenta_id) VALUES ('cli_incompleto', 'Incompleto', 'contador_1')")
            conn.execute("""
                INSERT OR REPLACE INTO clientes_config (cliente_id, razon_social_prompt, drive_folder_inbox, drive_folder_processed, drive_folder_error, activo)
                VALUES ('cli_incompleto', 'Prompt Incompleto', 'folder_inbox_x', '', 'folder_err_x', 1)
            """)

        res_inc = self.client.post(
            '/api/facturas/upload',
            data={'client_id': 'cli_incompleto', 'files': (io.BytesIO(pdf_bytes), 'f.pdf')},
            content_type='multipart/form-data',
            headers=self.auth_h(self.token_c1)
        )
        self.assertEqual(res_inc.status_code, 400)
        self.assertIn('incompleta', res_inc.get_json().get('error', '').lower())

        # 3. Más de 10 archivos por petición
        res_limit = self.client.post(
            '/api/facturas/upload',
            data={'client_id': 'cli_tenant_1', 'files': [(io.BytesIO(pdf_bytes), f'f_{i}.pdf') for i in range(11)]},
            content_type='multipart/form-data',
            headers=self.auth_h(self.token_c1)
        )
        self.assertEqual(res_limit.status_code, 400)
        self.assertIn('máximo', res_limit.get_json().get('error', '').lower())

    # ══════════════════════════════════════════════════════════════════════
    #  21. Validación matemática de simular_tabla
    # ══════════════════════════════════════════════════════════════════════

    def test_26_simular_tabla_validation(self):
        """Verifica validación estricta de rangos en simular_tabla (plazo 1-600, tasa 0-100, principal>0)."""
        # Plazo inválido (0 o 601)
        r1 = self.client.post('/api/contabilidad/pasivos/simular-tabla', json={'principal': 1000, 'tasa_anual': 12, 'plazo_meses': 0}, headers=self.auth_h(self.token_c1))
        self.assertEqual(r1.status_code, 400)

        r2 = self.client.post('/api/contabilidad/pasivos/simular-tabla', json={'principal': 1000, 'tasa_anual': 12, 'plazo_meses': 601}, headers=self.auth_h(self.token_c1))
        self.assertEqual(r2.status_code, 400)

        # Tasa inválida (<0 o >100)
        r3 = self.client.post('/api/contabilidad/pasivos/simular-tabla', json={'principal': 1000, 'tasa_anual': -5, 'plazo_meses': 12}, headers=self.auth_h(self.token_c1))
        self.assertEqual(r3.status_code, 400)

        r4 = self.client.post('/api/contabilidad/pasivos/simular-tabla', json={'principal': 1000, 'tasa_anual': 105, 'plazo_meses': 12}, headers=self.auth_h(self.token_c1))
        self.assertEqual(r4.status_code, 400)

        # Principal negativo o cero
        r5 = self.client.post('/api/contabilidad/pasivos/simular-tabla', json={'principal': 0, 'tasa_anual': 12, 'plazo_meses': 12}, headers=self.auth_h(self.token_c1))
        self.assertEqual(r5.status_code, 400)

        # Valores válidos
        r_ok = self.client.post('/api/contabilidad/pasivos/simular-tabla', json={'principal': 5000000, 'tasa_anual': 15.5, 'plazo_meses': 48}, headers=self.auth_h(self.token_c1))
        self.assertEqual(r_ok.status_code, 200)
        body = r_ok.get_json()
        self.assertTrue(body.get('ok'))
        cuotas = body.get('cuotas') or body.get('data', {}).get('cuotas', [])
        self.assertEqual(len(cuotas), 48)

    # ══════════════════════════════════════════════════════════════════════
    #  22. Estáticos: /static/ users.json, db, .env, storage retornan 404
    # ══════════════════════════════════════════════════════════════════════

    def test_27_static_files_hardening(self):
        """Verifica que cualquier acceso a /static/ de archivos sensibles devuelva 404."""
        for path in ('/static/users.json', '/static/facturami.db', '/static/.env', '/static/storage/x.jpg'):
            res = self.client.get(path)
            self.assertEqual(res.status_code, 404, f"{path} debe devolver 404")

    # ══════════════════════════════════════════════════════════════════════
    #  23. Sync Pull: Aislamiento estricto de n8n_creds y cuentas vacías
    # ══════════════════════════════════════════════════════════════════════

    def test_28_sync_pull_no_n8n_creds_and_empty_accounts(self):
        """
        Verifica que sync/pull no entregue n8n_creds a usuarios no administradores,
        falle con ValueError si falta cuenta_id, y entregue n8n_creds únicamente al admin.
        """
        with database.get_db(TEST_DB) as conn:
            database.config_set(conn, 'n8n_creds', json.dumps({'webhook_secret': 'super_secret_internal'}))
            # Directo en base de datos: sync_pull sin cuenta_id debe lanzar ValueError
            with self.assertRaises(ValueError):
                database.sync_pull(conn, None)
            with self.assertRaises(ValueError):
                database.sync_pull(conn, '   ')

            # Por defecto include_creds=False no incluye n8n_creds
            data_db = database.sync_pull(conn, 'contador_1')
            self.assertNotIn('n8n_creds', data_db)

        # Contador 1 pide pull por HTTP
        r_c1 = self.client.get('/api/sync/pull', headers=self.auth_h(self.token_c1))
        self.assertEqual(r_c1.status_code, 200)
        data_c1 = r_c1.get_json().get('data', {})
        self.assertNotIn('n8n_creds', data_c1, "n8n_creds NUNCA debe entregarse a contadores no-admin")

        # Admin pide pull por HTTP: n8n_creds no se entrega tampoco
        r_admin = self.client.get('/api/sync/pull', headers=self.auth_h(self.token_admin))
        self.assertEqual(r_admin.status_code, 200)
        data_admin = r_admin.get_json().get('data', {})
        self.assertNotIn('n8n_creds', data_admin, "n8n_creds debe estar totalmente ausente de sync_pull")

    # ══════════════════════════════════════════════════════════════════════
    #  24. Admin Transferir Cliente: validación atómica y tabla audit_log
    # ══════════════════════════════════════════════════════════════════════

    def test_29_admin_transferir_cliente_checks(self):
        """Verifica que transferir-cliente valide destino, rechace misma cuenta y registre en audit_log."""
        # 1. Transferir a cuenta inexistente -> 404
        r_no = self.client.post(
            '/api/admin/transferir-cliente',
            json={'cliente_id': 'cli_tenant_1', 'nuevo_cuenta_id': 'cuenta_inexistente_xyz'},
            headers=self.auth_h(self.token_admin)
        )
        self.assertEqual(r_no.status_code, 404)

        # 2. Transferir a cuenta inactiva / suspendida -> 400
        r_susp = self.client.post(
            '/api/admin/transferir-cliente',
            json={'cliente_id': 'cli_tenant_1', 'nuevo_cuenta_id': 'contador_suspendido'},
            headers=self.auth_h(self.token_admin)
        )
        self.assertEqual(r_susp.status_code, 400)
        self.assertIn('no está activa', r_susp.get_json().get('error', ''))

        # 3. Transferir a la misma cuenta -> 400
        r_same = self.client.post(
            '/api/admin/transferir-cliente',
            json={'cliente_id': 'cli_tenant_1', 'nuevo_cuenta_id': 'contador_1'},
            headers=self.auth_h(self.token_admin)
        )
        self.assertEqual(r_same.status_code, 400)
        self.assertIn('ya pertenece', r_same.get_json().get('error', ''))

        # 4. Transferir con cliente_id inválido -> 400
        r_bad_id = self.client.post(
            '/api/admin/transferir-cliente',
            json={'cliente_id': 'cli@@invalid$$', 'nuevo_cuenta_id': 'contador_2'},
            headers=self.auth_h(self.token_admin)
        )
        self.assertEqual(r_bad_id.status_code, 400)

        # 5. Caso exitoso: transferir válidamente a contador_2 -> 200
        r_ok = self.client.post(
            '/api/admin/transferir-cliente',
            json={'cliente_id': 'cli_tenant_1', 'nuevo_cuenta_id': 'contador_2'},
            headers=self.auth_h(self.token_admin)
        )
        self.assertEqual(r_ok.status_code, 200)

        # Verificar fila en clientes actualizada y entrada inmutable en audit_log
        with database.get_db(TEST_DB) as conn:
            c = conn.execute("SELECT cuenta_id FROM clientes WHERE id='cli_tenant_1'").fetchone()
            self.assertEqual(c['cuenta_id'], 'contador_2')

            log = conn.execute("SELECT * FROM audit_log WHERE target_tipo='cliente' AND target_id='cli_tenant_1' ORDER BY id DESC LIMIT 1").fetchone()
            self.assertIsNotNone(log)
            self.assertEqual(log['accion'], 'transferencia_cliente')
            self.assertEqual(log['actor'], 'admin_user')

            # Revertir transferencia a contador_1 para mantener estado limpio
            conn.execute("UPDATE clientes SET cuenta_id='contador_1' WHERE id='cli_tenant_1'")

    # ══════════════════════════════════════════════════════════════════════
    #  25. Login con Email asigna Username Canónico en JWT y clientes
    # ══════════════════════════════════════════════════════════════════════

    def test_30_jwt_identity_canonical_username_on_email_login(self):
        """Verifica que al hacer login con email, la identidad del token JWT sea el username canónico en minúsculas."""
        import bcrypt
        pw = "Password123!"
        pw_hash = bcrypt.hashpw(pw.encode('utf-8'), bcrypt.gensalt(rounds=12)).decode('utf-8')
        
        # Registrar cuenta de prueba con email
        with database.get_db(TEST_DB) as conn:
            conn.execute("""
                INSERT OR REPLACE INTO cuentas (username, password_hash, nombre, email, role, activo)
                VALUES ('carlos_contador', ?, 'Carlos Contador', 'carlos@estudiocontable.com', 'contador', 1)
            """, (pw_hash,))
            
        with open(TEST_USERS, 'r', encoding='utf-8') as f:
            current_users = json.load(f)
        current_users.append({
            "username": "carlos_contador", "email": "carlos@estudiocontable.com",
            "password_hash": pw_hash, "role": "contador", "name": "Carlos Contador", "active": True
        })
        with open(TEST_USERS, 'w', encoding='utf-8') as f:
            json.dump(current_users, f)

        # Login con email en mayúsculas/minúsculas
        res = self.client.post('/api/login', json={'username': 'Carlos@EstudioContable.com', 'password': pw})
        self.assertEqual(res.status_code, 200)
        token_carlos = res.get_json().get('token')
        self.assertTrue(token_carlos)

        # Usar token para registrar cliente en sync/push
        res_push = self.client.post(
            '/api/sync/push',
            headers=self.auth_h(token_carlos),
            json={
                'f_clients': [{'id': 'cli_carlos_01', 'name': 'Empresa de Carlos', 'type': 'PJ'}]
            }
        )
        self.assertEqual(res_push.status_code, 200)

        # Verificar en SQLite que el cliente quedó asociado al username canónico 'carlos_contador', NO al email
        with database.get_db(TEST_DB) as conn:
            cli = conn.execute("SELECT cuenta_id FROM clientes WHERE id='cli_carlos_01'").fetchone()
            self.assertEqual(cli['cuenta_id'], 'carlos_contador')

    # ══════════════════════════════════════════════════════════════════════
    #  26. Rollback Atómico en Sync Push con Clave f_data_ Inválida
    # ══════════════════════════════════════════════════════════════════════

    def test_31_sync_push_atomic_rollback_on_invalid_f_data(self):
        """
        Verifica que si sync/push recibe un cliente nuevo válido en f_clients pero
        una clave f_data_ inválida o maliciosa, la transacción haga rollback completo
        y el cliente nuevo NO quede registrado en la base de datos.
        """
        cid_nuevo = "cli_rollback_target_999"
        res = self.client.post(
            '/api/sync/push',
            headers=self.auth_h(self.token_c1),
            json={
                'f_clients': [{'id': cid_nuevo, 'name': 'Cliente Que Debe Fallar', 'type': 'PF'}],
                'f_data_invalid@@@#$': {'ingresos': {'rows': []}}
            }
        )
        self.assertEqual(res.status_code, 400)

        # Verificar que el cliente NO existe en base de datos
        with database.get_db(TEST_DB) as conn:
            row = conn.execute("SELECT id FROM clientes WHERE id=?", (cid_nuevo,)).fetchone()
            self.assertIsNone(row, "El cliente NO debe haberse insertado tras un fallo en f_data (rollback atómico)")

    # ══════════════════════════════════════════════════════════════════════
    #  27. /api/auth/verify Rechaza Cuentas Suspendidas
    # ══════════════════════════════════════════════════════════════════════

    def test_32_auth_verify_rejects_suspended_account(self):
        """Verifica que /api/auth/verify con un token de una cuenta suspendida devuelva 403 o 401."""
        res_susp = self.client.get('/api/auth/verify', headers=self.auth_h(self.token_susp))
        self.assertIn(res_susp.status_code, (401, 403))

        # En cambio, con cuenta activa devuelve 200
        res_ok = self.client.get('/api/auth/verify', headers=self.auth_h(self.token_c1))
        self.assertEqual(res_ok.status_code, 200)
        self.assertEqual(res_ok.get_json().get('user'), 'contador_1')

    # ══════════════════════════════════════════════════════════════════════
    #  28. Prevención de Colisión y Secuestro de ID en insert_asiento
    # ══════════════════════════════════════════════════════════════════════

    def test_33_asiento_id_collision_rejected_cross_client(self):
        """Verifica que no se pueda insertar un asiento con un ID ya perteneciente a otro cliente."""
        lineas = [
            {'debito': 1000, 'credito': 0, 'cuenta_codigo': '1.1.1.01'},
            {'debito': 0, 'credito': 1000, 'cuenta_codigo': '2.1.1.01'}
        ]
        shared_asnt_id = "asnt_seguridad_cross_client_01"

        with database.get_db(TEST_DB) as conn:
            # 1. Insertar para cli_tenant_1
            database.insert_asiento(conn, {
                'id': shared_asnt_id,
                'cliente_id': 'cli_tenant_1',
                'concepto': 'Asiento Legítimo Cliente 1'
            }, lineas)

            # 2. Intentar secuestrar o colisionar con cli_tenant_2 usando el mismo ID
            with self.assertRaises(PermissionError):
                database.insert_asiento(conn, {
                    'id': shared_asnt_id,
                    'cliente_id': 'cli_tenant_2',
                    'concepto': 'Asiento Malicioso Cliente 2'
                }, lineas)

    # ══════════════════════════════════════════════════════════════════════
    #  29. Rate Limiting Persistente en SQLite para Subidas
    # ══════════════════════════════════════════════════════════════════════

    def test_34_upload_rate_limit_persistent_sqlite(self):
        """Verifica que el límite de 300 archivos/hora persista en la tabla uploads_log de SQLite."""
        cuenta_test = "rate_limit_test_user"
        with database.get_db(TEST_DB) as conn:
            # Limpiar historial de prueba
            conn.execute("DELETE FROM uploads_log WHERE cuenta_id=?", (cuenta_test,))

            # Subida de 295 archivos -> Permitida
            ok1, total1, max_h = database.check_and_record_upload_rate(conn, cuenta_test, 295, max_archivos_hora=300)
            self.assertTrue(ok1)
            self.assertEqual(total1, 295)

            # Subida subsecuente de 10 archivos (295 + 10 = 305 > 300) -> Rechazada
            ok2, total2, max_h = database.check_and_record_upload_rate(conn, cuenta_test, 10, max_archivos_hora=300)
            self.assertFalse(ok2)
            self.assertEqual(total2, 295)

        # Probar endpoint HTTP /api/facturas/upload con 429 cuando el límite se satura
        with database.get_db(TEST_DB) as conn:
            # Llenar la cuota de contador_1
            conn.execute("INSERT INTO uploads_log (cuenta_id, num_archivos, created_at) VALUES ('contador_1', 300, datetime('now'))")

        pdf_bytes = b"%PDF-1.4\n%test\n%%EOF"
        res_429 = self.client.post(
            '/api/facturas/upload',
            data={'client_id': 'cli_tenant_1', 'files': (io.BytesIO(pdf_bytes), 'f.pdf')},
            content_type='multipart/form-data',
            headers=self.auth_h(self.token_c1)
        )
        self.assertEqual(res_429.status_code, 429)
        self.assertIn('Límite de subidas por hora excedido', res_429.get_json().get('error', ''))

        # Limpiar uploads_log de contador_1 para no afectar otras pruebas
        with database.get_db(TEST_DB) as conn:
            conn.execute("DELETE FROM uploads_log WHERE cuenta_id='contador_1'")

    # ══════════════════════════════════════════════════════════════════════
    #  30. Normalización de ID en sync_push y upsert_cliente
    # ══════════════════════════════════════════════════════════════════════

    def test_35_sync_push_id_normalization_and_numeric_id(self):
        """Verifica que un id con espacio '999 ' sea normalizado a '999' y que ids numéricos se persistan bien."""
        res = self.client.post(
            '/api/sync/push',
            headers=self.auth_h(self.token_c1),
            json={
                'f_clients': [
                    {'id': '999 ', 'name': 'Cliente Con Espacio', 'type': 'PF'},
                    {'id': '8888', 'name': 'Cliente Numérico', 'type': 'PJ'}
                ]
            }
        )
        self.assertEqual(res.status_code, 200)

        with database.get_db(TEST_DB) as conn:
            # Debe existir '999' limpio, no '999 '
            c_space = conn.execute("SELECT id FROM clientes WHERE id='999'").fetchone()
            self.assertIsNotNone(c_space)
            c_raw = conn.execute("SELECT id FROM clientes WHERE id='999 '").fetchone()
            self.assertIsNone(c_raw)

            # Debe existir '8888'
            c_num = conn.execute("SELECT id FROM clientes WHERE id='8888'").fetchone()
            self.assertIsNotNone(c_num)

    # ══════════════════════════════════════════════════════════════════════
    #  31. delete_contador Bloquea con 409 si Hay Clientes
    # ══════════════════════════════════════════════════════════════════════

    def test_36_delete_contador_blocked_with_409_if_has_clients(self):
        """Verifica que no se pueda eliminar una cuenta que todavía tiene clientes asociados."""
        # contador_1 tiene cli_tenant_1
        r_del = self.client.delete('/api/admin/contadores/contador_1', headers=self.auth_h(self.token_admin))
        self.assertEqual(r_del.status_code, 409)
        self.assertIn('transfiérelos primero', r_del.get_json().get('error', '').lower())

    # ══════════════════════════════════════════════════════════════════════
    #  32. create_contador Rechaza si Existen Clientes con ese cuenta_id
    # ══════════════════════════════════════════════════════════════════════

    def test_37_create_contador_rejected_if_clients_exist_with_cuenta_id(self):
        """Verifica que no se pueda registrar una cuenta nueva con un username que tiene clientes huérfanos."""
        with database.get_db(TEST_DB) as conn:
            conn.execute("INSERT OR REPLACE INTO clientes (id, nombre, cuenta_id) VALUES ('cli_orphan_99', 'Huerfano', 'contador_fantasma')")

        r_create = self.client.post(
            '/api/admin/contadores',
            json={
                'username': 'contador_fantasma',
                'password': 'Password123!',
                'nombre': 'Fantasma',
                'email': 'fantasma@test.com'
            },
            headers=self.auth_h(self.token_admin)
        )
        self.assertEqual(r_create.status_code, 409)
        self.assertIn('existen 1 cliente(s)', r_create.get_json().get('error', ''))

    # ══════════════════════════════════════════════════════════════════════
    #  33. Flujo Post-Transferencia: Pull limpia y Push no da error
    # ══════════════════════════════════════════════════════════════════════

    def test_38_client_transfer_flow_previous_accountant_pull_push(self):
        """
        Verifica el ciclo de vida tras transferencia de cliente:
        1. Contador 1 transfiere cli_transfer_flow a Contador 2.
        2. Contador 1 hace pull -> cli_transfer_flow ya no viene.
        3. Contador 1 hace push con sus clientes legítimos -> 200 OK sin error.
        4. Si Contador 1 intenta forzar push de cli_transfer_flow -> 403 Forbidden.
        """
        with database.get_db(TEST_DB) as conn:
            conn.execute("INSERT OR REPLACE INTO clientes (id, nombre, cuenta_id) VALUES ('cli_transfer_flow', 'Flujo Transfer', 'contador_1')")

        # Admin transfiere a contador_2
        r_tr = self.client.post(
            '/api/admin/transferir-cliente',
            json={'cliente_id': 'cli_transfer_flow', 'nuevo_cuenta_id': 'contador_2'},
            headers=self.auth_h(self.token_admin)
        )
        self.assertEqual(r_tr.status_code, 200)

        # Contador 1 hace pull
        r_pull = self.client.get('/api/sync/pull', headers=self.auth_h(self.token_c1))
        self.assertEqual(r_pull.status_code, 200)
        c1_clients = [c['id'] for c in r_pull.get_json()['data']['f_clients']]
        self.assertNotIn('cli_transfer_flow', c1_clients)

        # Contador 1 hace push normal sin el cliente transferido -> 200 OK
        r_push = self.client.post(
            '/api/sync/push',
            headers=self.auth_h(self.token_c1),
            json={'f_clients': [{'id': 'cli_tenant_1', 'name': 'Cliente 1', 'type': 'PF'}]}
        )
        self.assertEqual(r_push.status_code, 200)

        # Si Contador 1 intenta mandar de vuelta el cliente transferido -> 403 Forbidden
        r_push_bad = self.client.post(
            '/api/sync/push',
            headers=self.auth_h(self.token_c1),
            json={'f_clients': [{'id': 'cli_transfer_flow', 'name': 'Flujo Transfer', 'type': 'PF'}]}
        )
        self.assertEqual(r_push_bad.status_code, 403)

    # ══════════════════════════════════════════════════════════════════════
    #  34. insert_transaccion: Prevención de Colisión y Secuestro de ID
    # ══════════════════════════════════════════════════════════════════════

    def test_39_insert_transaccion_collision_protection(self):
        """Verifica que insert_transaccion no permita insertar un id ya existente perteneciente a otro cliente."""
        tx_custom_id = "tx_secuestro_test_99"
        row_data = {'id': tx_custom_id, 'fecha': '2026-11-01', 'total': 1000}

        with database.get_db(TEST_DB) as conn:
            # 1. Insertar para cli_tenant_1
            database.insert_transaccion(conn, 'cli_tenant_1', 'ingresos', row_data)

            # 2. Intentar secuestrar con cli_tenant_2 usando el mismo id
            with self.assertRaises(PermissionError):
                database.insert_transaccion(conn, 'cli_tenant_2', 'ingresos', row_data)

    # ══════════════════════════════════════════════════════════════════════
    #  35. Cuota: activo NULL Tratado como Activo (Migraciones Antiguas)
    # ══════════════════════════════════════════════════════════════════════

    def test_40_cuota_null_activo_treated_as_active(self):
        """Verifica que check_quota_for_client trate activo NULL como cuenta activa y activo=0 como inactiva."""
        with database.get_db(TEST_DB) as conn:
            conn.execute("INSERT OR REPLACE INTO cuentas (username, nombre, activo) VALUES ('cuenta_null_act', 'Null Act', NULL)")
            conn.execute("INSERT OR REPLACE INTO clientes (id, nombre, cuenta_id) VALUES ('cli_null_act', 'Cli Null', 'cuenta_null_act')")

        from routes.facturas import check_quota_for_client
        ok_null, _ = check_quota_for_client('cli_null_act')
        self.assertTrue(ok_null, "activo NULL debe tratarse como activo")

        # Desactivar cuenta
        with database.get_db(TEST_DB) as conn:
            conn.execute("UPDATE cuentas SET activo=0 WHERE username='cuenta_null_act'")

        ok_zero, err_zero = check_quota_for_client('cli_null_act')
        self.assertFalse(ok_zero, "activo=0 debe rechazarse")
        self.assertIn('inactiva', err_zero.lower())

    # ══════════════════════════════════════════════════════════════════════
    #  36. Aislamiento estricto: La BD de pruebas NUNCA debe ser la del repo
    # ══════════════════════════════════════════════════════════════════════

    def test_41_database_path_must_not_match_repository_db(self):
        """Verifica que la base de datos usada por los tests sea temporal y no coincida con facturami.db del repo."""
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        repo_db = os.path.abspath(os.path.join(repo_root, 'facturami.db'))
        active_db = os.path.abspath(database.DB_PATH)

        self.assertNotEqual(
            active_db, repo_db,
            f"FALLO CRÍTICO DE AISLAMIENTO: Los tests están usando la BD del repo: {active_db}"
        )
        self.assertTrue(
            active_db.startswith(tempfile.gettempdir()) or 'test' in os.path.basename(active_db),
            f"La ruta de BD activa ({active_db}) no reside en un directorio temporal seguro"
        )

    # ══════════════════════════════════════════════════════════════════════
    #  37. Cuenta '__all__' y eliminación de bypass en get_clientes_legacy
    # ══════════════════════════════════════════════════════════════════════

    def test_42_cuenta_all_no_recibe_clientes_ajenos(self):
        """Verifica que una cuenta o consulta con '__all__' no devuelva clientes de otros contadores."""
        with database.get_db(TEST_DB) as conn:
            # 1. get_clientes con cuenta_id='__all__' no debe devolver los clientes de contador_1 ni contador_2
            cli_all = database.get_clientes(conn, cuenta_id='__all__')
            self.assertEqual(cli_all, [], "cuenta_id='__all__' NO debe ser un bypass global")

            # 2. get_clientes_legacy con '__all__' debe devolver lista vacía si no existe cliente con cuenta_id='__all__'
            legacy_all = database.get_clientes_legacy(conn, cuenta_id='__all__')
            self.assertEqual(legacy_all, [], "get_clientes_legacy con '__all__' NO debe devolver clientes ajenos")

            # 3. get_all_clientes_admin() sí devuelve todos los clientes
            all_admin = database.get_all_clientes_admin(conn)
            self.assertGreater(len(all_admin), 0, "get_all_clientes_admin debe listar clientes para administración")

        # 4. Validar regex de nombres de usuario en create_contador
        r_bad_user = self.client.post(
            '/api/admin/contadores',
            headers=self.auth_h(self.token_admin),
            json={'username': 'INVALID USER!', 'password': 'password123', 'nombre': 'Bad User'}
        )
        self.assertEqual(r_bad_user.status_code, 400)
        self.assertIn('entre 3 y 32 caracteres', r_bad_user.get_json().get('error', ''))

        r_short_user = self.client.post(
            '/api/admin/contadores',
            headers=self.auth_h(self.token_admin),
            json={'username': 'ab', 'password': 'password123', 'nombre': 'Short'}
        )
        self.assertEqual(r_short_user.status_code, 400)

    # ══════════════════════════════════════════════════════════════════════
    #  38. audit_log: Inmutabilidad append-only mediante triggers SQLite
    # ══════════════════════════════════════════════════════════════════════

    def test_43_audit_log_append_only_triggers(self):
        """Verifica que los triggers de SQLite bloqueen cualquier intento de UPDATE o DELETE en audit_log."""
        with database.get_db(TEST_DB) as conn:
            database.insert_audit_log(
                conn,
                accion='test_inmutabilidad',
                actor='admin_test',
                target_tipo='cuenta',
                target_id='c_target',
                detalles={'prueba': True}
            )
            row = conn.execute("SELECT id FROM audit_log WHERE accion='test_inmutabilidad'").fetchone()
            log_id = row['id']

            # Intentar UPDATE debe fallar por el trigger
            with self.assertRaises((sqlite3.IntegrityError, sqlite3.OperationalError)) as ctx_update:
                conn.execute("UPDATE audit_log SET accion='manipulado' WHERE id=?", (log_id,))
            self.assertIn('inmutable append-only', str(ctx_update.exception))

            # Intentar DELETE debe fallar por el trigger
            with self.assertRaises((sqlite3.IntegrityError, sqlite3.OperationalError)) as ctx_delete:
                conn.execute("DELETE FROM audit_log WHERE id=?", (log_id,))
            self.assertIn('inmutable append-only', str(ctx_delete.exception))

    # ══════════════════════════════════════════════════════════════════════
    #  39. Registro de auditoría en todas las rutas administrativas
    # ══════════════════════════════════════════════════════════════════════

    def test_44_admin_routes_audit_log_entries(self):
        """Verifica que todas las operaciones admin (alta, suspensión, password, extender, reset, baja) registren en audit_log."""
        target_u = "contador_audit_test"

        # 1. Alta (create_contador)
        r_create = self.client.post(
            '/api/admin/contadores',
            headers=self.auth_h(self.token_admin),
            json={'username': target_u, 'password': 'secret_password_123', 'nombre': 'Audit Test User', 'plan_id': 'emprendedor'}
        )
        self.assertEqual(r_create.status_code, 201)

        # 2. Suspensión y cambio de contraseña (update_contador)
        r_up = self.client.put(
            f'/api/admin/contadores/{target_u}',
            headers=self.auth_h(self.token_admin),
            json={'activo': 0, 'password': 'new_secret_password_456'}
        )
        self.assertEqual(r_up.status_code, 200)

        # 3. Extender suscripción
        r_ext = self.client.post(
            f'/api/admin/contadores/{target_u}/extender-suscripcion',
            headers=self.auth_h(self.token_admin),
            json={'dias': 60}
        )
        self.assertEqual(r_ext.status_code, 200)

        # 4. Reset facturas
        r_res = self.client.post(
            f'/api/admin/contadores/{target_u}/reset-facturas',
            headers=self.auth_h(self.token_admin)
        )
        self.assertEqual(r_res.status_code, 200)

        # 5. Baja (delete_contador)
        r_del = self.client.delete(
            f'/api/admin/contadores/{target_u}',
            headers=self.auth_h(self.token_admin)
        )
        self.assertEqual(r_del.status_code, 200)

        # Verificar que todas las acciones quedaron en audit_log
        with database.get_db(TEST_DB) as conn:
            logs = conn.execute(
                "SELECT accion FROM audit_log WHERE target_id=? ORDER BY id ASC",
                (target_u,)
            ).fetchall()
            acciones = [r['accion'] for r in logs]

            self.assertIn('crear_cuenta', acciones)
            self.assertIn('suspender_cuenta', acciones)
            self.assertIn('cambio_password', acciones)
            self.assertIn('actualizar_cuenta', acciones)
            self.assertIn('extender_suscripcion', acciones)
            self.assertIn('reset_facturas', acciones)
            self.assertIn('eliminar_cuenta', acciones)

    # ══════════════════════════════════════════════════════════════════════
    #  40. sync_push: Respuesta 403 incluye cliente_id ofensor
    # ══════════════════════════════════════════════════════════════════════

    def test_45_sync_push_403_returns_cliente_id(self):
        """Verifica que cuando sync/push rechaza un cliente ajeno, devuelva 403 y el campo cliente_id."""
        r = self.client.post(
            '/api/sync/push',
            headers=self.auth_h(self.token_c2),
            json={'f_data_cli_tenant_1': {'ingresos': {'rows': []}}}
        )
        self.assertEqual(r.status_code, 403)
        res_data = r.get_json()
        self.assertFalse(res_data['ok'])
        self.assertEqual(res_data.get('cliente_id'), 'cli_tenant_1')

    # ══════════════════════════════════════════════════════════════════════
    #  41. audit_log: Sanitización y Redacción Total de Contraseñas y Hashes
    # ══════════════════════════════════════════════════════════════════════

    def test_46_audit_log_redacts_passwords_and_hashes(self):
        """Verifica que insert_audit_log nunca almacene contraseñas en texto claro ni hashes en detalles_json."""
        sensitive_payload = {
            'username': 'usuario_sensible',
            'password': 'PasswordSuperSecreta123!',
            'new_password': 'OtraPassword456!',
            'password_hash': '$2b$12$eX4mpleBcryptHashThatMustNeverBeSavedInAuditLog12345678',
            'api_secret': 'super_secret_token_value',
            'plan_id': 'corporativo'
        }

        with database.get_db(TEST_DB) as conn:
            database.insert_audit_log(
                conn,
                accion='test_sanitizacion',
                actor='admin_user',
                target_tipo='cuenta',
                target_id='usuario_sensible',
                detalles=sensitive_payload
            )

            row = conn.execute(
                "SELECT detalles_json FROM audit_log WHERE accion='test_sanitizacion' ORDER BY id DESC LIMIT 1"
            ).fetchone()

            self.assertIsNotNone(row)
            detalles_raw = row['detalles_json']

            # 1. Asegurar que ningún valor sensible aparezca en el JSON guardado
            self.assertNotIn('PasswordSuperSecreta123!', detalles_raw)
            self.assertNotIn('OtraPassword456!', detalles_raw)
            self.assertNotIn('$2b$12$', detalles_raw)
            self.assertNotIn('super_secret_token_value', detalles_raw)

            # 2. Asegurar que las claves sensibles fueron redactadas a [REDACTED]
            detalles = json.loads(detalles_raw)
            self.assertEqual(detalles['password'], '[REDACTED]')
            self.assertEqual(detalles['new_password'], '[REDACTED]')
            self.assertEqual(detalles['password_hash'], '[REDACTED]')
            self.assertEqual(detalles['api_secret'], '[REDACTED]')
            # El campo no sensible debe mantenerse intacto
            self.assertEqual(detalles['plan_id'], 'corporativo')


    # ══════════════════════════════════════════════════════════════════════
    #  42. Ruta de soporte admin: solo metadatos de clientes + audit_log
    # ══════════════════════════════════════════════════════════════════════

    def test_47_admin_clientes_contador_solo_metadatos_y_auditoria(self):
        """Contador 403, anonimo 401, audit_log escrito (tambien si no existe) y sin campos de contenido."""
        url = '/api/admin/contadores/contador_1/clientes'

        # Anonimo -> 401
        self.assertEqual(self.client.get(url).status_code, 401)
        # Contador regular -> 403
        r_c = self.client.get(url, headers=self.auth_h(self.token_c1))
        self.assertEqual(r_c.status_code, 403)

        with database.get_db(TEST_DB) as conn:
            antes = conn.execute(
                "SELECT COUNT(*) FROM audit_log WHERE accion='consulta_clientes_contador'"
            ).fetchone()[0]
            conn.execute(
                "UPDATE clientes SET telefono='8888-0000', email='secreto@ficticio.test', "
                "direccion='Calle Ficticia 1', webhook_url='http://ficticio.test/hook' "
                "WHERE LOWER(TRIM(cuenta_id))='contador_1'"
            )

        # Admin -> 200, solo campos permitidos
        r = self.client.get(url, headers=self.auth_h(self.token_admin))
        self.assertEqual(r.status_code, 200)
        body = r.get_json()
        self.assertGreater(len(body['clientes']), 0)
        permitidos = {'id', 'nombre', 'tipo', 'automatizacion_activa', 'facturas_pendientes'}
        for c in body['clientes']:
            self.assertEqual(set(c.keys()), permitidos)
        raw = r.get_data(as_text=True)
        for prohibido in ('8888-0000', 'secreto@ficticio.test', 'Calle Ficticia', 'ficticio.test/hook',
                          'telefono', 'email', 'direccion', 'webhook', 'transacciones', 'asientos'):
            self.assertNotIn(prohibido, raw)

        # Cuenta inexistente -> 404 y tambien se audita
        r404 = self.client.get('/api/admin/contadores/cuenta_que_no_existe/clientes',
                               headers=self.auth_h(self.token_admin))
        self.assertEqual(r404.status_code, 404)

        with database.get_db(TEST_DB) as conn:
            filas = conn.execute(
                "SELECT target_id, actor, detalles_json FROM audit_log "
                "WHERE accion='consulta_clientes_contador' ORDER BY id"
            ).fetchall()
            self.assertEqual(len(filas) - antes, 2)
            self.assertEqual(filas[-2]['target_id'], 'contador_1')
            self.assertEqual(filas[-2]['actor'], 'admin_user')
            self.assertEqual(filas[-1]['target_id'], 'cuenta_que_no_existe')
            self.assertNotIn('8888-0000', filas[-2]['detalles_json'])


if __name__ == '__main__':
    unittest.main()
