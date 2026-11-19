"""
Tests FASE 5: Facturación Electrónica (Emisión, XML v4.3/v4.4, Firma Digital XAdES-BES, PDF, Consecutivos y Clave).
Todos los datos son ficticios. Base de datos temporal aislada.
"""
import base64
import os
import tempfile
import unittest

os.environ.setdefault('JWT_SECRET_KEY', 'test-jwt-secret-key-0123456789-abcdef')
os.environ.setdefault('N8N_INTERNAL_SECRET', 'test-n8n-secret-key-0123456789-abcdef')
os.environ['FACTURAMI_FE_SECRET_KEY'] = 'test-fe-secret-key-0123456789-abcdefghij'
os.environ['FACTURAMI_FE_SIMULATE_HACIENDA'] = '1'  # Modo simulación para pruebas sin red

from flask_jwt_extended import create_access_token
import db as database
from app import create_app
import services.secret_store as secret_store
from services.fe_clave_service import generar_consecutivo, generar_clave, desglosar_clave
from services.fe_xml_service import generate_electronic_invoice_xml
from services.fe_signer_service import sign_xml_xades_bes, verify_xml_signature


def _make_p12(pin: str):
    """Genera un certificado .p12 autofirmado para pruebas unitarias de firma digital."""
    import datetime
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.serialization import pkcs12
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'EMPRESA PRUEBA FE')])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=365))
            .sign(key, hashes.SHA256()))

    return pkcs12.serialize_key_and_certificates(
        b'test-fe', key, cert, None, serialization.BestAvailableEncryption(pin.encode())
    )


class TestFase5VentasFacturacion(unittest.TestCase):
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
            for u in ('dueno_a', 'dueno_b', 'vendedor_a', 'cajero_a'):
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

        # Asignar vendedor en Alfa
        self.c.post('/api/ventas/users', headers=self.h_a,
                    json={'company_id': '3101111111', 'username': 'vendedor_a', 'role': 'vendedor'})

        # Inicializar contexto y cargar certificado en Empresa Alfa
        self.c.get('/api/ventas/context?company_id=3101111111', headers=self.h_a)
        self.p12_bytes = _make_p12('1234')
        self.c.put('/api/ventas/config/hacienda', headers=self.h_a, json={
            'company_id': '3101111111',
            'username': 'atv-test-user',
            'password': 'atv-test-password',
            'pin': '1234',
            'cert_p12_b64': base64.b64encode(self.p12_bytes).decode('ascii')
        })

    def tearDown(self):
        database.DB_PATH = self.prev_db_path
        try:
            os.remove(self.tmp_db)
        except OSError:
            pass

    # ── Consecutivo y Clave Numérica de 50 Dígitos ────────────────────
    def test_01_generacion_consecutivo_y_clave_50_digitos(self):
        # Consecutivo: 3 (sucursal) + 5 (terminal) + 2 (tipo) + 10 (secuencia)
        cons = generar_consecutivo('001', '00001', '01', 1)
        self.assertEqual(len(cons), 20)
        self.assertEqual(cons, '00100001010000000001')

        cons_te = generar_consecutivo('001', '00002', '04', 45)
        self.assertEqual(cons_te, '00100002040000000045')

        # Clave 50 dígitos
        clave = generar_clave('3101111111', cons, situacion='1', codigo_seguridad='12345678')
        self.assertEqual(len(clave), 50)
        self.assertTrue(clave.isdigit())
        self.assertTrue(clave.startswith('506'))  # Código país Costa Rica

        # Desglose de la clave
        info = desglosar_clave(clave)
        self.assertEqual(info['pais'], '506')
        self.assertEqual(info['cedula_emisor'], '3101111111')
        self.assertEqual(info['consecutivo'], cons)
        self.assertEqual(info['tipo_documento'], '01')
        self.assertEqual(info['tipo_documento_nombre'], 'Factura Electrónica')
        self.assertEqual(info['codigo_seguridad'], '12345678')

    # ── Generación de XML Oficial v4.3 ─────────────────────────────────
    def test_02_generacion_xml_v43_factura_y_tiquete(self):
        doc_data = {
            'clave_numerica': '50618112600310111111100100001010000000001112345678',
            'consecutivo': '00100001010000000001',
            'tipo_documento': '01',
            'condicion_venta': '01',
            'medio_pago': '01',
            'moneda': 'CRC',
            'tipo_cambio': 1.0,
            'fecha_emision': '2026-11-18T12:00:00-06:00'
        }
        emisor = {
            'razon_social': 'Empresa Comercial Alfa S.A.',
            'identificacion': '3101111111',
            'tipo_identificacion': '02',
            'actividad_principal': '620100',
            'provincia': '1', 'canton': '01', 'distrito': '01',
            'otras_senas': 'Calle Central',
            'telefono': '22223333',
            'email': 'facturacion@alfa.test'
        }
        receptor = {
            'nombre': 'Cliente Receptor Ficticio',
            'identificacion': '102340567',
            'tipo_identificacion': '01',
            'email': 'cliente@receptor.test'
        }
        lineas = [{
            'numero_linea': 1,
            'codigo_interno': 'SKU-001',
            'codigo_cabys': '5411100000000',
            'descripcion': 'Servicio de Consultoría de Software',
            'cantidad': 2,
            'unidad_medida': 'Sp',
            'precio_unitario': 50000,
            'tarifa_iva_codigo': '08',
            'tarifa_iva_porcentaje': 13.0
        }]

        xml = generate_electronic_invoice_xml(doc_data, emisor, receptor, lineas)
        self.assertIn('<FacturaElectronica', xml)
        self.assertIn('xmlns="https://cdn.comprobanteselectronicos.go.cr/xml-schemas/v4.3/facturaElectronica"', xml)
        self.assertIn('<Clave>50618112600310111111100100001010000000001112345678</Clave>', xml)
        self.assertIn('<NumeroConsecutivo>00100001010000000001</NumeroConsecutivo>', xml)
        self.assertIn('<CodigoActividad>620100</CodigoActividad>', xml)
        self.assertIn('<TotalVenta>100000.00</TotalVenta>', xml)
        self.assertIn('<TotalImpuesto>13000.00</TotalImpuesto>', xml)
        self.assertIn('<TotalComprobante>113000.00</TotalComprobante>', xml)

    # ── Firma Digital XAdES-BES ───────────────────────────────────────
    def test_03_firmado_digital_xades_bes(self):
        doc_data = {
            'clave_numerica': '50618112600310111111100100001010000000001112345678',
            'consecutivo': '00100001010000000001',
            'tipo_documento': '01',
            'condicion_venta': '01',
            'medio_pago': '01',
            'moneda': 'CRC',
            'tipo_cambio': 1.0,
            'fecha_emision': '2026-11-18T12:00:00-06:00'
        }
        emisor = {'razon_social': 'Alfa S.A.', 'identificacion': '3101111111', 'actividad_principal': '123456'}
        lineas = [{'cantidad': 1, 'precio_unitario': 10000, 'tarifa_iva_codigo': '08', 'tarifa_iva_porcentaje': 13.0, 'codigo_cabys': '1234567890123'}]

        xml_crudo = generate_electronic_invoice_xml(doc_data, emisor, None, lineas)
        signed_xml = sign_xml_xades_bes(xml_crudo, self.p12_bytes, '1234')

        self.assertIn('<ds:Signature', signed_xml)
        self.assertIn('<ds:SignedInfo', signed_xml)
        self.assertIn('<ds:SignatureValue', signed_xml)
        self.assertIn('<ds:KeyInfo', signed_xml)
        self.assertIn('<xades:QualifyingProperties', signed_xml)
        self.assertIn('<xades:SignedProperties', signed_xml)

        # Verificación criptográfica de la firma digital
        self.assertTrue(verify_xml_signature(signed_xml))

    # ── Emisión de Factura Electrónica vía API ─────────────────────────
    def test_04_emision_factura_api_y_consecutivo_atomico(self):
        # 1. Registrar producto en catálogo que controla inventario
        r_prod = self.c.post('/api/ventas/products', headers=self.h_a, json={
            'company_id': '3101111111',
            'codigo_interno': 'ITEM-INV-1',
            'codigo_cabys': '1234567890123',
            'nombre': 'Producto con Existencia',
            'precio_venta': 25000,
            'tarifa_iva_codigo': '08',
            'controla_inventario': 1,
            'existencia': 10,
            'stock_minimo': 2
        })
        self.assertEqual(r_prod.status_code, 200)
        prod = r_prod.get_json()['data']

        # 2. Registrar cliente
        r_cli = self.c.post('/api/ventas/customers', headers=self.h_a, json={
            'company_id': '3101111111',
            'tipo_identificacion': '01',
            'identificacion': '109870654',
            'nombre': 'Cliente Factura Contado'
        })
        self.assertEqual(r_cli.status_code, 200)
        cli = r_cli.get_json()['data']

        # 3. Emitir Factura Electrónica (01)
        r_fact = self.c.post('/api/ventas/documents', headers=self.h_a, json={
            'company_id': '3101111111',
            'tipo_documento': '01',
            'customer_id': cli['id'],
            'condicion_venta': '01',  # Contado
            'medio_pago': '01',
            'lineas': [{
                'product_id': prod['id'],
                'cantidad': 3,
                'precio_unitario': 25000,
                'tarifa_iva_codigo': '08',
                'codigo_cabys': '1234567890123'
            }]
        })
        self.assertEqual(r_fact.status_code, 200)
        doc1 = r_fact.get_json()['data']
        self.assertEqual(doc1['consecutivo'], '00100001010000000001')
        self.assertEqual(len(doc1['clave_numerica']), 50)
        # Subtotal: 75 000, IVA 13%: 9 750, Total: 84 750
        self.assertEqual(doc1['subtotal'], 75000.0)
        self.assertEqual(doc1['total_impuestos'], 9750.0)
        self.assertEqual(doc1['total_comprobante'], 84750.0)
        self.assertEqual(doc1['estado_comprobante'], 'enviado')

        # 4. Verificar que la existencia del producto bajó de 10 a 7
        p_check = self.c.get(f"/api/ventas/products/{prod['id']}?company_id=3101111111", headers=self.h_a).get_json()['data']
        self.assertEqual(p_check['existencia'], 7.0)

        # 5. Emitir segunda factura: el consecutivo DEBE ser 00100001010000000002
        r_fact2 = self.c.post('/api/ventas/documents', headers=self.h_a, json={
            'company_id': '3101111111',
            'tipo_documento': '01',
            'customer_id': cli['id'],
            'condicion_venta': '01',
            'lineas': [{
                'cantidad': 1,
                'precio_unitario': 10000,
                'tarifa_iva_codigo': '08',
                'codigo_cabys': '1234567890123'
            }]
        })
        self.assertEqual(r_fact2.status_code, 200)
        doc2 = r_fact2.get_json()['data']
        self.assertEqual(doc2['consecutivo'], '00100001010000000002')

    # ── Venta a Crédito y Creación de Cuenta por Cobrar ────────────────
    def test_05_venta_credito_crea_cuenta_por_cobrar_automatica(self):
        r_cli = self.c.post('/api/ventas/customers', headers=self.h_a, json={
            'company_id': '3101111111',
            'tipo_identificacion': '02',
            'identificacion': '3101888777',
            'nombre': 'Cliente Crédito Empresarial',
            'limite_credito': 500000
        })
        cli_id = r_cli.get_json()['data']['id']

        # Emitir factura a crédito (condición '06', plazo 30 días)
        r = self.c.post('/api/ventas/documents', headers=self.h_a, json={
            'company_id': '3101111111',
            'tipo_documento': '01',
            'customer_id': cli_id,
            'condicion_venta': '06',
            'plazo_credito': 30,
            'lineas': [{
                'cantidad': 2,
                'precio_unitario': 100000,
                'tarifa_iva_codigo': '08',
                'codigo_cabys': '1234567890123'
            }]
        })
        self.assertEqual(r.status_code, 200)
        doc = r.get_json()['data']
        # 200 000 * 1.13 = 226 000
        self.assertEqual(doc['total_comprobante'], 226000.0)

        # Consultar el cliente: debe tener saldo pendiente de 226 000 y crédito disponible de 274 000
        c_check = self.c.get(f'/api/ventas/customers/{cli_id}?company_id=3101111111', headers=self.h_a).get_json()['data']
        self.assertEqual(c_check['saldo_pendiente'], 226000.0)
        self.assertEqual(c_check['credito_disponible'], 274000.0)

    # ── Nota de Crédito Electrónica (03) con Referencia Obligatoria ───
    def test_06_nota_credito_exige_referencia(self):
        # 1. Intentar emitir nota de crédito sin referencia -> Error 400
        r_bad = self.c.post('/api/ventas/documents', headers=self.h_a, json={
            'company_id': '3101111111',
            'tipo_documento': '03',
            'lineas': [{'cantidad': 1, 'precio_unitario': 5000, 'codigo_cabys': '1234567890123'}]
        })
        self.assertEqual(r_bad.status_code, 400)

        # 2. Emitir nota de crédito con referencia completa
        r_nc = self.c.post('/api/ventas/documents', headers=self.h_a, json={
            'company_id': '3101111111',
            'tipo_documento': '03',
            'referencia': {
                'tipo_documento': '01',
                'numero': '50618112600310111111100100001010000000001112345678',
                'fecha_emision': '2026-11-18T10:00:00-06:00',
                'codigo': '01',
                'razon': 'Anula factura por devolución de mercancía'
            },
            'lineas': [{
                'cantidad': 1,
                'precio_unitario': 5000,
                'tarifa_iva_codigo': '08',
                'codigo_cabys': '1234567890123'
            }]
        })
        self.assertEqual(r_nc.status_code, 200)
        nc = r_nc.get_json()['data']
        self.assertEqual(nc['tipo_documento'], '03')
        self.assertEqual(nc['consecutivo'], '00100001030000000001')
        self.assertIn('<NotaCreditoElectronica', nc['xml_firmado'])
        self.assertIn('<InformacionReferencia', nc['xml_firmado'])

    # ── Descarga de PDF y XML con Validación de Formato ───────────────
    def test_07_descarga_pdf_y_xml_comprobante(self):
        # Emitir tiquete electrónico (04)
        r = self.c.post('/api/ventas/documents', headers=self.h_a, json={
            'company_id': '3101111111',
            'tipo_documento': '04',
            'lineas': [{'cantidad': 1, 'precio_unitario': 15000, 'tarifa_iva_codigo': '08', 'codigo_cabys': '1234567890123'}]
        })
        doc_id = r.get_json()['data']['id']

        # Descarga de XML
        r_xml = self.c.get(f'/api/ventas/documents/{doc_id}/xml?company_id=3101111111', headers=self.h_a)
        self.assertEqual(r_xml.status_code, 200)
        self.assertIn('application/xml', r_xml.headers['Content-Type'])
        self.assertIn('<TiqueteElectronico', r_xml.get_data(as_text=True))

        # Descarga de PDF (comprobar encabezado PDF binario %PDF-)
        r_pdf = self.c.get(f'/api/ventas/documents/{doc_id}/pdf?company_id=3101111111', headers=self.h_a)
        self.assertEqual(r_pdf.status_code, 200)
        self.assertIn('application/pdf', r_pdf.headers['Content-Type'])
        self.assertTrue(r_pdf.data.startswith(b'%PDF-'))

    # ── Aislamiento Multiempresa Estricto ──────────────────────────────
    def test_08_aislamiento_multiempresa_documentos(self):
        r = self.c.post('/api/ventas/documents', headers=self.h_a, json={
            'company_id': '3101111111',
            'tipo_documento': '01',
            'lineas': [{'cantidad': 1, 'precio_unitario': 20000, 'codigo_cabys': '1234567890123'}]
        })
        doc_id = r.get_json()['data']['id']

        # Empresa Beta NO puede consultar el documento de Alfa por ID
        self.assertEqual(self.c.get(f'/api/ventas/documents/{doc_id}?company_id=3101222222', headers=self.h_b).status_code, 404)
        # Empresa Beta NO puede descargar el PDF ni XML de Alfa
        self.assertEqual(self.c.get(f'/api/ventas/documents/{doc_id}/pdf?company_id=3101222222', headers=self.h_b).status_code, 404)
        self.assertEqual(self.c.get(f'/api/ventas/documents/{doc_id}/xml?company_id=3101222222', headers=self.h_b).status_code, 404)
        # Empresa Beta no puede listar documentos con company_id ajeno -> 403
        self.assertEqual(self.c.get(f'/api/ventas/documents?company_id=3101111111', headers=self.h_b).status_code, 403)

    # ── Sincronización y Consulta ante Hacienda ────────────────────────
    def test_09_sincronizacion_estado_hacienda(self):
        r = self.c.post('/api/ventas/documents', headers=self.h_a, json={
            'company_id': '3101111111',
            'tipo_documento': '01',
            'lineas': [{'cantidad': 1, 'precio_unitario': 10000, 'codigo_cabys': '1234567890123'}]
        })
        doc_id = r.get_json()['data']['id']

        r_sync = self.c.post(f'/api/ventas/documents/{doc_id}/sync-hacienda', headers=self.h_a, json={'company_id': '3101111111'})
        self.assertEqual(r_sync.status_code, 200)
        data = r_sync.get_json()['data']
        self.assertEqual(data['estado_comprobante'], 'aceptado')
        self.assertIn('<MensajeHacienda', data['xml_respuesta_hacienda'])

    # ── Validación de Interfaz HTML y Funciones JS de Facturación ──────
    def test_10_estructura_ui_html_facturacion(self):
        with open('FACTURAMI.html', 'r', encoding='utf-8') as f:
            html = f.read()

        # Comprobar contenedor principal y paneles de Facturación
        self.assertIn('id="vpg-v-facturacion"', html)
        self.assertIn('id="v-fe-form-panel"', html)
        self.assertIn('id="v-fe-history-panel"', html)

        # Comprobar inputs y selectores del comprobante
        self.assertIn('id="v_fe_tipo_doc"', html)
        self.assertIn('id="v_fe_cliente_sel"', html)
        self.assertIn('id="v_fe_cond_venta"', html)
        self.assertIn('id="v_fe_medio_pago"', html)
        self.assertIn('id="v_fe_moneda"', html)
        self.assertIn('id="v_fe_tipo_cambio"', html)
        self.assertIn('id="v_fe_plazo_credito"', html)

        # Comprobar bloque de información de referencia
        self.assertIn('id="v_fe_ref_container"', html)
        self.assertIn('id="v_fe_ref_tipo"', html)
        self.assertIn('id="v_fe_ref_numero"', html)
        self.assertIn('id="v_fe_ref_codigo"', html)
        self.assertIn('id="v_fe_ref_razon"', html)

        # Comprobar totales dinámicos
        self.assertIn('id="v_fe_subtotal"', html)
        self.assertIn('id="v_fe_descuento"', html)
        self.assertIn('id="v_fe_impuestos"', html)
        self.assertIn('id="v_fe_total"', html)

        # Comprobar modal dialog de resultado de emisión
        self.assertIn('id="vModalFeResultado"', html)

        # Comprobar funciones JavaScript de Fase 5
        for fn in ('vOpenFacturacionSub', 'vEnsureFeDependencies', 'vOnFeClienteChange',
                   'vTogglePlazoCredito', 'vToggleTipoCambio', 'vAddFeLine', 'vRemoveFeLine',
                   'vOnProductSelectedInLine', 'vRenderFeLines', 'vCalcFeTotals', 'vResetFeForm',
                   'vEmitirComprobante', 'vShowFeResultadoModal', 'vLoadFeDocuments',
                   'vFilterFeHistory', 'vSyncHacienda', 'vDownloadXml', 'vDownloadPdf',
                   'vPrepareNotaCreditoFromDoc'):
            self.assertIn(f'function {fn}', html)


if __name__ == '__main__':
    unittest.main()
