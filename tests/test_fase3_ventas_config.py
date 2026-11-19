"""
Tests FASE 3: configuración multiempresa, roles/permisos y secretos cifrados.
Todos los datos son ficticios. Usa una base temporal.
"""
import base64
import os
import tempfile
import unittest

os.environ.setdefault('JWT_SECRET_KEY', 'test-jwt-secret-key-0123456789-abcdef')
os.environ.setdefault('N8N_INTERNAL_SECRET', 'test-n8n-secret-key-0123456789-abcdef')
os.environ['FACTURAMI_FE_SECRET_KEY'] = 'test-fe-secret-key-0123456789-abcdefghij'

from flask_jwt_extended import create_access_token
import db as database
from app import create_app

try:
    import cryptography  # noqa: F401
    HAS_CRYPTO = True
except Exception:
    HAS_CRYPTO = False


def _make_p12(pin: str, days_valid=365):
    """Genera un .p12 ficticio autofirmado para pruebas."""
    import datetime
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.serialization import pkcs12
    from cryptography.hazmat.primitives.asymmetric import rsa
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'EMPRESA FICTICIA DE PRUEBA')])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=days_valid))
            .sign(key, hashes.SHA256()))
    return pkcs12.serialize_key_and_certificates(
        b'test', key, cert, None, serialization.BestAvailableEncryption(pin.encode()))


class TestFase3VentasConfig(unittest.TestCase):
    def setUp(self):
        fd, self.tmp_db = tempfile.mkstemp(suffix='.db')
        os.close(fd)
        self.prev_db_path = database.DB_PATH
        database.DB_PATH = self.tmp_db
        database.init_db(self.tmp_db)
        self.app = create_app()
        self.c = self.app.test_client()

        with database.get_db(self.tmp_db) as conn:
            for u in ('dueno_a', 'dueno_b', 'vendedor1', 'cajero1', 'contador1'):
                database.upsert_cuenta(conn, {'username': u, 'nombre': u, 'role': 'contador', 'activo': 1})
            database.upsert_cliente(conn, {'id': '3101111111', 'type': 'PJ', 'name': 'Empresa Alfa Ficticia',
                                           'mail': 'a@alfa.test'}, cuenta_id='dueno_a')
            database.upsert_cliente(conn, {'id': '3101222222', 'type': 'PJ', 'name': 'Empresa Beta Ficticia',
                                           'mail': 'b@beta.test'}, cuenta_id='dueno_b')

        with self.app.app_context():
            tok = lambda u, r='contador': {'Authorization': 'Bearer ' + create_access_token(
                identity=u, additional_claims={'role': r})}
            self.h_a, self.h_b = tok('dueno_a'), tok('dueno_b')
            self.h_vend, self.h_caj, self.h_cont = tok('vendedor1'), tok('cajero1'), tok('contador1')
            self.h_admin = tok('facturami1', 'admin')

    def tearDown(self):
        database.DB_PATH = self.prev_db_path
        try:
            os.remove(self.tmp_db)
        except OSError:
            pass

    def _asignar(self, user, role, company='3101111111'):
        return self.c.post('/api/ventas/users', headers=self.h_a,
                           json={'company_id': company, 'username': user, 'role': role})

    # ── Aislamiento y roles ────────────────────────────────────────────
    def test_01_config_aislada_por_empresa(self):
        r = self.c.get('/api/ventas/config?company_id=3101111111', headers=self.h_b)
        self.assertEqual(r.status_code, 403)
        r = self.c.put('/api/ventas/config', headers=self.h_b,
                       json={'company_id': '3101111111', 'telefono': '8888-0000'})
        self.assertEqual(r.status_code, 403)
        r = self.c.get('/api/ventas/config?company_id=3101111111', headers=self.h_a)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()['data']['role'], 'admin_empresa')

    def test_02_admin_global_no_accede_al_contenido(self):
        r = self.c.get('/api/ventas/config?company_id=3101111111', headers=self.h_admin)
        self.assertEqual(r.status_code, 403)
        r = self.c.get('/api/ventas/dashboard?company_id=3101111111', headers=self.h_admin)
        self.assertEqual(r.status_code, 403)

    def test_03_roles_y_permisos(self):
        self.assertEqual(self._asignar('vendedor1', 'vendedor').status_code, 200)
        self.assertEqual(self._asignar('cajero1', 'cajero').status_code, 200)
        self.assertEqual(self._asignar('contador1', 'contador').status_code, 200)

        q = '?company_id=3101111111'
        # Todos ven el dashboard
        for h in (self.h_vend, self.h_caj, self.h_cont):
            self.assertEqual(self.c.get('/api/ventas/dashboard' + q, headers=h).status_code, 200)
        # Vendedor y cajero NO ven la configuración; contador sí (solo lectura)
        self.assertEqual(self.c.get('/api/ventas/config' + q, headers=self.h_vend).status_code, 403)
        self.assertEqual(self.c.get('/api/ventas/config' + q, headers=self.h_caj).status_code, 403)
        self.assertEqual(self.c.get('/api/ventas/config' + q, headers=self.h_cont).status_code, 200)
        # Nadie salvo el admin de empresa edita configuración, secretos ni usuarios
        for h in (self.h_vend, self.h_caj, self.h_cont):
            self.assertEqual(self.c.put('/api/ventas/config', headers=h,
                             json={'company_id': '3101111111', 'telefono': '1'}).status_code, 403)
            self.assertEqual(self.c.put('/api/ventas/config/hacienda', headers=h,
                             json={'company_id': '3101111111', 'username': 'x'}).status_code, 403)
            self.assertEqual(self.c.get('/api/ventas/users' + q, headers=h).status_code, 403)
        # Un usuario asignado a la empresa A no ve la empresa B
        self.assertEqual(self.c.get('/api/ventas/dashboard?company_id=3101222222',
                                    headers=self.h_vend).status_code, 403)

    def test_04_usuario_desactivado_pierde_acceso(self):
        self._asignar('vendedor1', 'vendedor')
        q = '?company_id=3101111111'
        self.assertEqual(self.c.get('/api/ventas/dashboard' + q, headers=self.h_vend).status_code, 200)
        r = self.c.delete('/api/ventas/users/vendedor1' + q, headers=self.h_a)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.c.get('/api/ventas/dashboard' + q, headers=self.h_vend).status_code, 403)

    def test_05_asignacion_invalida(self):
        self.assertEqual(self._asignar('no_existe_x', 'vendedor').status_code, 404)
        self.assertEqual(self._asignar('vendedor1', 'superjefe').status_code, 400)
        self.assertEqual(self._asignar('dueno_a', 'cajero').status_code, 400)

    # ── Configuración general ──────────────────────────────────────────
    def test_06_editar_config_general_con_validacion_y_auditoria(self):
        r = self.c.put('/api/ventas/config', headers=self.h_a, json={
            'company_id': '3101111111', 'telefono': '2222-3333', 'moneda_default': 'USD',
            'email': 'ventas@alfa.test', 'plazo_credito_default': 45})
        self.assertEqual(r.status_code, 200)
        s = self.c.get('/api/ventas/config?company_id=3101111111', headers=self.h_a).get_json()['data']['settings']
        self.assertEqual(s['telefono'], '2222-3333')
        self.assertEqual(s['moneda_default'], 'USD')
        self.assertEqual(s['plazo_credito_default'], 45)
        # Validaciones
        bad = lambda **kw: self.c.put('/api/ventas/config', headers=self.h_a,
                                      json={'company_id': '3101111111', **kw}).status_code
        self.assertEqual(bad(email='no-es-correo'), 400)
        self.assertEqual(bad(moneda_default='EUR'), 400)
        self.assertEqual(bad(plazo_credito_default=9999), 400)
        self.assertEqual(bad(), 400)
        with database.get_db(self.tmp_db) as conn:
            n = conn.execute("SELECT COUNT(*) FROM audit_log WHERE accion='ventas_config_general'").fetchone()[0]
        self.assertEqual(n, 1)

    def test_07_consecutivos_solo_antes_de_emitir(self):
        self.c.get('/api/ventas/config?company_id=3101111111', headers=self.h_a)
        body = {'company_id': '3101111111', 'codigo_sucursal': '001', 'codigo_terminal': '00002', 'consec_fe': 100}
        self.assertEqual(self.c.put('/api/ventas/config/terminal', headers=self.h_a, json=body).status_code, 200)
        with database.get_db(self.tmp_db) as conn:
            conn.execute("""INSERT INTO electronic_documents (id, company_id, tipo_documento, consecutivo,
                            fecha_emision, estado_comprobante) VALUES ('d1','3101111111','01','x','2026-01-01','generado')""")
        self.assertEqual(self.c.put('/api/ventas/config/terminal', headers=self.h_a, json=body).status_code, 409)
        bad = dict(body, codigo_terminal='12')
        self.assertEqual(self.c.put('/api/ventas/config/terminal', headers=self.h_a, json=bad).status_code, 400)

    # ── Secretos ───────────────────────────────────────────────────────
    @unittest.skipUnless(HAS_CRYPTO, 'requiere la libreria cryptography')
    def test_08_secretos_cifrados_nunca_expuestos(self):
        p12 = base64.b64encode(_make_p12('1234')).decode()
        r = self.c.put('/api/ventas/config/hacienda', headers=self.h_a, json={
            'company_id': '3101111111', 'username': 'usuario-atv-ficticio', 'password': 'Clave-Secreta-XYZ',
            'pin': '1234', 'cert_p12_b64': p12})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        raw = r.get_data(as_text=True)
        for secreto in ('Clave-Secreta-XYZ', p12[:40], 'usuario-atv-ficticio'):
            self.assertNotIn(secreto, raw)
        s = r.get_json()['data']['settings']
        self.assertTrue(s['has_cert_file'])
        self.assertTrue(s['has_hacienda_password'])
        self.assertTrue(s['cert_expira'])
        for k in ('hacienda_password_enc', 'cert_pin_enc', 'cert_file_enc'):
            self.assertNotIn(k, s)
        # En la base NO hay texto plano
        with database.get_db(self.tmp_db) as conn:
            row = dict(conn.execute("SELECT * FROM company_fe_settings WHERE company_id='3101111111'").fetchone())
            audit = ' '.join(r2['detalles_json'] for r2 in conn.execute(
                "SELECT detalles_json FROM audit_log WHERE accion='ventas_config_hacienda'"))
        blob = ' '.join(str(v) for v in row.values())
        self.assertNotIn('Clave-Secreta-XYZ', blob)
        self.assertNotIn(p12[:40], blob)
        self.assertNotIn('Clave-Secreta-XYZ', audit)
        self.assertIn('certificado', audit)

    @unittest.skipUnless(HAS_CRYPTO, 'requiere la libreria cryptography')
    def test_09_cifrado_aislado_por_empresa(self):
        import services.secret_store as ss
        token = ss.encrypt('3101111111', 'secreto')
        self.assertEqual(ss.decrypt('3101111111', token), b'secreto')
        with self.assertRaises(ss.SecretStoreError):
            ss.decrypt('3101222222', token)

    @unittest.skipUnless(HAS_CRYPTO, 'requiere la libreria cryptography')
    def test_10_pin_incorrecto_o_cert_invalido_rechazado(self):
        p12 = base64.b64encode(_make_p12('1234')).decode()
        r = self.c.put('/api/ventas/config/hacienda', headers=self.h_a,
                       json={'company_id': '3101111111', 'pin': '9999', 'cert_p12_b64': p12})
        self.assertEqual(r.status_code, 400)
        r = self.c.put('/api/ventas/config/hacienda', headers=self.h_a,
                       json={'company_id': '3101111111', 'pin': '1234',
                             'cert_p12_b64': base64.b64encode(b'basura').decode()})
        self.assertEqual(r.status_code, 400)

    def test_11_sin_clave_maestra_no_guarda_nada(self):
        prev = os.environ.pop('FACTURAMI_FE_SECRET_KEY')
        try:
            r = self.c.put('/api/ventas/config/hacienda', headers=self.h_a,
                           json={'company_id': '3101111111', 'password': 'abc'})
            self.assertEqual(r.status_code, 503)
            with database.get_db(self.tmp_db) as conn:
                row = conn.execute("SELECT hacienda_password_enc FROM company_fe_settings "
                                   "WHERE company_id='3101111111'").fetchone()
            self.assertTrue(row is None or not row['hacienda_password_enc'])
        finally:
            os.environ['FACTURAMI_FE_SECRET_KEY'] = prev

    @unittest.skipUnless(HAS_CRYPTO, 'requiere la libreria cryptography')
    def test_12_produccion_exige_credenciales_completas(self):
        r = self.c.put('/api/ventas/config/hacienda', headers=self.h_a,
                       json={'company_id': '3101111111', 'ambiente': 'prod'})
        self.assertEqual(r.status_code, 400)


if __name__ == '__main__':
    unittest.main()
