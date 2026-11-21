"""
FACTURAMI - Servicio Transaccional de Correo Electrónico (SMTP) para Comprobantes Electrónicos.
================================================================================================
Genera y despacha correos oficiales a clientes receptores con:
  1. Representación gráfica oficial en PDF con código QR.
  2. Archivo XML firmado digitalmente (XAdES-BES).
  3. Archivo XML de respuesta de Hacienda (si ya fue aceptado).
  4. Plantilla HTML corporativa y responsive con el desglose de la transacción.
Soporta configuración SMTP personalizada por empresa, fallback global (.env) y modo simulación.
"""
import email.utils
import os
import re
import smtplib
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import db as database
import services.secret_store as secret_store
from services.fe_emission_service import download_document_pdf, download_document_xml, get_document
from services.fe_clave_service import TIPOS_COMPROBANTE

EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')


class MailError(Exception):
    pass


def is_mail_simulation_mode() -> bool:
    """Modo simulación activo en pruebas unitarias o cuando se fuerza por variable de entorno."""
    return (
        os.environ.get('FACTURAMI_FE_SIMULATE_HACIENDA', '').lower() in ('1', 'true', 'yes') or
        os.environ.get('FACTURAMI_MAIL_SIMULATE', '').lower() in ('1', 'true', 'yes')
    )


def get_company_smtp_config(company_id: str, db_path=None) -> dict:
    """
    Obtiene la configuración SMTP para la empresa.
    Prioridad:
      1. Configuración activa de la empresa en company_fe_settings (si smtp_enabled=1).
      2. Variables de entorno globales del servidor (.env).
      3. Modo simulación (en tests o entornos de desarrollo).
    """
    cid = str(company_id or '').strip()
    with database.get_db(db_path) as conn:
        row = conn.execute("SELECT * FROM company_fe_settings WHERE company_id = ?", (cid,)).fetchone()
        st = dict(row) if row else {}

    # 1. Configuración por empresa
    if st.get('smtp_enabled') and st.get('smtp_host'):
        pwd = ''
        if st.get('smtp_password_enc') and secret_store.is_configured():
            try:
                pwd = secret_store.decrypt(cid, st['smtp_password_enc']).decode('utf-8')
            except Exception:
                pwd = ''
        return {
            'source': 'company',
            'host': st.get('smtp_host'),
            'port': int(st.get('smtp_port') or 587),
            'user': st.get('smtp_user') or '',
            'password': pwd,
            'from_name': st.get('smtp_from_name') or st.get('nombre_comercial') or st.get('razon_social') or 'FACTURAMI',
            'from_email': st.get('smtp_user') or st.get('correo_envio_comprobantes') or st.get('email') or 'facturacion@facturami.app',
            'tls': bool(st.get('smtp_tls', 1)),
            'enabled': True,
            'simulated': False
        }

    # 2. Configuración global por variables de entorno
    env_host = os.environ.get('SMTP_HOST')
    if env_host:
        return {
            'source': 'env',
            'host': env_host,
            'port': int(os.environ.get('SMTP_PORT', 587)),
            'user': os.environ.get('SMTP_USER', ''),
            'password': os.environ.get('SMTP_PASSWORD', ''),
            'from_name': os.environ.get('SMTP_FROM_NAME', 'FACTURAMI Facturación Electrónica'),
            'from_email': os.environ.get('SMTP_FROM_EMAIL') or os.environ.get('SMTP_USER') or 'facturacion@facturami.app',
            'tls': os.environ.get('SMTP_TLS', '1').lower() in ('1', 'true', 'yes'),
            'enabled': True,
            'simulated': False
        }

    # 3. Modo simulación (fallback para tests)
    if is_mail_simulation_mode():
        return {
            'source': 'simulation',
            'host': 'smtp.simulation.local',
            'port': 587,
            'user': 'simulated@facturami.app',
            'password': 'simulated-password',
            'from_name': st.get('nombre_comercial') or 'FACTURAMI Simulación',
            'from_email': st.get('correo_envio_comprobantes') or 'notificaciones@facturami.app',
            'tls': True,
            'enabled': True,
            'simulated': True
        }

    return {
        'source': 'none',
        'enabled': False,
        'simulated': False
    }


def test_smtp_connection(smtp_cfg: dict) -> dict:
    """
    Prueba la conexión a un servidor SMTP especificado.
    """
    if is_mail_simulation_mode() or smtp_cfg.get('simulated'):
        return {
            'ok': True,
            'mensaje': 'Conexión SMTP exitosa (Simulación)',
            'host': smtp_cfg.get('host'),
            'port': smtp_cfg.get('port')
        }

    host = smtp_cfg.get('host')
    port = int(smtp_cfg.get('port') or 587)
    user = smtp_cfg.get('user') or ''
    password = smtp_cfg.get('password') or ''
    use_tls = bool(smtp_cfg.get('tls', True))

    if not host:
        raise MailError("Se requiere el host del servidor SMTP")

    try:
        if port == 465:
            server = smtplib.SMTP_SSL(host, port, timeout=12)
        else:
            server = smtplib.SMTP(host, port, timeout=12)
            if use_tls:
                server.starttls()

        if user and password:
            server.login(user, password)

        server.quit()
        return {
            'ok': True,
            'mensaje': f'Conexión SMTP exitosa con {host}:{port}',
            'host': host,
            'port': port
        }
    except Exception as e:
        raise MailError(f"Error conectando al servidor SMTP ({host}:{port}): {e}")


def _fmt_currency(monto, moneda='CRC'):
    sym = '₡' if moneda == 'CRC' else '$'
    try:
        val = float(monto or 0.0)
        return f"{sym}{val:,.2f}".replace(',', ' ')
    except (ValueError, TypeError):
        return f"{sym}0.00"


def build_document_email_content(doc: dict, emisor: dict) -> tuple:
    """
    Construye (asunto, cuerpo_texto, cuerpo_html) para el correo de notificación.
    """
    tipo_doc = doc.get('tipo_documento', '01')
    tipo_nombre = TIPOS_COMPROBANTE.get(tipo_doc, 'Comprobante Electrónico')
    consecutivo = doc.get('consecutivo', '')
    clave = doc.get('clave_numerica', '')
    emisor_nombre = emisor.get('nombre_comercial') or emisor.get('razon_social') or emisor.get('nombre') or 'Emisor Electrónico'
    receptor_nombre = doc.get('customer_nombre') or 'Estimado Cliente'
    moneda = doc.get('moneda', 'CRC')
    total = _fmt_currency(doc.get('total_comprobante'), moneda)
    subtotal = _fmt_currency(doc.get('subtotal'), moneda)
    iva = _fmt_currency(doc.get('total_impuestos'), moneda)
    fecha = doc.get('fecha_emision', '')[:19].replace('T', ' ')

    asunto = f"{tipo_nombre} #{consecutivo} - {emisor_nombre}"

    texto_plano = f"""
Estimado(a) {receptor_nombre},

{emisor_nombre} le ha emitido un comprobante electrónico oficial:

- Documento: {tipo_nombre}
- Consecutivo: {consecutivo}
- Clave Numérica: {clave}
- Fecha de Emisión: {fecha}
- Subtotal: {subtotal}
- Impuestos (IVA): {iva}
- Total: {total}

Adjunto a este correo encontrará los archivos oficiales:
1. {consecutivo}.pdf: Representación gráfica oficial del comprobante.
2. {clave}.xml: Archivo XML firmado digitalmente conforme a los requisitos del Ministerio de Hacienda de Costa Rica.

Gracias por su preferencia.
Emitido mediante FACTURAMI - Plataforma de Facturación y Contabilidad Electrónica.
""".strip()

    cuerpo_html = f"""
<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{asunto}</title>
</head>
<body style="margin: 0; padding: 0; background-color: #f4f6f9; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; color: #2d3748;">
  <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background-color: #f4f6f9; padding: 30px 15px;">
    <tr>
      <td align="center">
        <table role="presentation" width="100%" style="max-width: 600px; background-color: #ffffff; border-radius: 12px; overflow: hidden; box-shadow: 0 4px 16px rgba(0,0,0,0.06); border: 1px solid #e2e8f0;">
          <!-- Header -->
          <tr>
            <td style="background: linear-gradient(135deg, #0d9488 0%, #0f766e 100%); padding: 28px 32px; color: #ffffff;">
              <h1 style="margin: 0 0 6px 0; font-size: 22px; font-weight: 700; letter-spacing: -0.5px;">{emisor_nombre}</h1>
              <p style="margin: 0; font-size: 13px; opacity: 0.9;">Comprobante Electrónico Oficial • Ministerio de Hacienda CR</p>
            </td>
          </tr>
          <!-- Body -->
          <tr>
            <td style="padding: 32px;">
              <p style="margin: 0 0 16px 0; font-size: 15px; color: #4a5568;">
                Hola <strong>{receptor_nombre}</strong>,
              </p>
              <p style="margin: 0 0 24px 0; font-size: 14px; line-height: 1.6; color: #4a5568;">
                Adjunto a este correo encontrará su <strong>{tipo_nombre}</strong> con número consecutivo <strong>{consecutivo}</strong>, generada conforme a las directrices de la Dirección General de Tributación de Costa Rica.
              </p>

              <!-- Resumen Tarjeta -->
              <table role="presentation" width="100%" style="background-color: #f8fafc; border-radius: 8px; border: 1px solid #e2e8f0; margin-bottom: 24px; padding: 18px 20px;">
                <tr>
                  <td style="font-size: 13px; color: #64748b; padding-bottom: 8px;">Número Consecutivo:</td>
                  <td align="right" style="font-size: 13px; font-weight: 600; color: #0f172a; padding-bottom: 8px;">{consecutivo}</td>
                </tr>
                <tr>
                  <td style="font-size: 13px; color: #64748b; padding-bottom: 8px;">Fecha de Emisión:</td>
                  <td align="right" style="font-size: 13px; color: #0f172a; padding-bottom: 8px;">{fecha}</td>
                </tr>
                <tr>
                  <td style="font-size: 13px; color: #64748b; padding-bottom: 8px;">Subtotal:</td>
                  <td align="right" style="font-size: 13px; color: #0f172a; padding-bottom: 8px;">{subtotal}</td>
                </tr>
                <tr>
                  <td style="font-size: 13px; color: #64748b; padding-bottom: 12px; border-bottom: 1px dashed #cbd5e1;">Impuestos (IVA):</td>
                  <td align="right" style="font-size: 13px; color: #0f172a; padding-bottom: 12px; border-bottom: 1px dashed #cbd5e1;">{iva}</td>
                </tr>
                <tr>
                  <td style="font-size: 15px; font-weight: 700; color: #0f766e; padding-top: 12px;">Total Comprobante:</td>
                  <td align="right" style="font-size: 18px; font-weight: 800; color: #0f766e; padding-top: 12px;">{total}</td>
                </tr>
              </table>

              <!-- Clave 50 dígitos -->
              <div style="background-color: #f1f5f9; border-radius: 6px; padding: 12px 14px; margin-bottom: 24px; font-family: monospace; font-size: 11px; color: #475569; word-break: break-all;">
                <span style="font-weight: bold; color: #1e293b;">Clave Numérica:</span><br>{clave}
              </div>

              <!-- Archivos Adjuntos -->
              <p style="margin: 0 0 10px 0; font-size: 13px; font-weight: 600; color: #334155;">Archivos adjuntos en este correo:</p>
              <ul style="margin: 0 0 24px 0; padding-left: 20px; font-size: 13px; color: #64748b; line-height: 1.6;">
                <li><strong>{consecutivo}.pdf</strong> — Representación gráfica oficial con código QR.</li>
                <li><strong>{clave}.xml</strong> — Comprobante electrónico firmado digitalmente (XAdES-BES).</li>
              </ul>

              <p style="margin: 0; font-size: 12px; color: #94a3b8; line-height: 1.5;">
                Este es un correo automático generado por el emisor mediante el sistema FACTURAMI. Si tiene consultas sobre el contenido del documento, por favor responda directamente a este correo.
              </p>
            </td>
          </tr>
          <!-- Footer -->
          <tr>
            <td style="background-color: #f8fafc; padding: 16px 32px; text-align: center; border-top: 1px solid #e2e8f0; font-size: 11px; color: #94a3b8;">
              FACTURAMI • Facturación Electrónica y Contabilidad NIIF • Costa Rica
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>
""".strip()

    return asunto, texto_plano, cuerpo_html


def send_document_email(company_id: str, document_id: str, to_email: str = None,
                        actor: str = '', db_path=None) -> dict:
    """
    Envía el comprobante electrónico por correo con el PDF y XML adjuntos.
    Retorna información del despacho y actualiza el estado en la base de datos.
    """
    cid = str(company_id or '').strip()
    did = str(document_id or '').strip()

    if not cid or not did:
        raise MailError("company_id y document_id son requeridos")

    # 1. Obtener documento y validar
    doc = get_document(cid, did)
    destinatario = str(to_email or doc.get('customer_email') or '').strip()

    if not destinatario:
        raise MailError("El cliente no tiene correo electrónico asignado y no se especificó destinatario")
    if not EMAIL_RE.match(destinatario):
        raise MailError(f"Dirección de correo '{destinatario}' no es válida")

    # 2. Obtener emisor
    with database.get_db(db_path) as conn:
        emisor_row = conn.execute("SELECT * FROM clientes WHERE id = ?", (cid,)).fetchone()
        fe_cfg_row = conn.execute("SELECT * FROM company_fe_settings WHERE company_id = ?", (cid,)).fetchone()
        emisor = {**(dict(emisor_row) if emisor_row else {}), **(dict(fe_cfg_row) if fe_cfg_row else {})}

    # 3. Obtener configuración SMTP
    smtp_cfg = get_company_smtp_config(cid, db_path=db_path)
    if not smtp_cfg.get('enabled'):
        raise MailError("El servicio de correo SMTP no está configurado ni habilitado para esta empresa")

    # 4. Generar adjuntos (PDF y XML)
    try:
        pdf_name, pdf_bytes = download_document_pdf(cid, did)
    except Exception as e_pdf:
        raise MailError(f"Error generando representación PDF para el correo: {e_pdf}")

    try:
        xml_name, xml_content = download_document_xml(cid, did)
    except Exception as e_xml:
        raise MailError(f"Error obteniendo XML del comprobante para el correo: {e_xml}")

    # 5. Construir mensaje MIME
    asunto, texto_plano, cuerpo_html = build_document_email_content(doc, emisor)

    msg = MIMEMultipart('mixed')
    msg['Subject'] = asunto
    from_name = smtp_cfg.get('from_name') or 'FACTURAMI'
    from_email = smtp_cfg.get('from_email') or 'notificaciones@facturami.app'
    msg['From'] = email.utils.formataddr((from_name, from_email))
    msg['To'] = destinatario
    msg['Date'] = email.utils.formatdate(localtime=True)

    # Parte alternativa con texto y html
    alt_part = MIMEMultipart('alternative')
    alt_part.attach(MIMEText(texto_plano, 'plain', 'utf-8'))
    alt_part.attach(MIMEText(cuerpo_html, 'html', 'utf-8'))
    msg.attach(alt_part)

    # Adjuntar PDF
    pdf_attach = MIMEApplication(pdf_bytes, _subtype="pdf")
    pdf_attach.add_header('Content-Disposition', 'attachment', filename=pdf_name)
    msg.attach(pdf_attach)

    # Adjuntar XML
    xml_bytes = xml_content.encode('utf-8') if isinstance(xml_content, str) else xml_content
    xml_attach = MIMEApplication(xml_bytes, _subtype="xml")
    xml_attach.add_header('Content-Disposition', 'attachment', filename=xml_name)
    msg.attach(xml_attach)

    # Adjuntar XML de respuesta de Hacienda si existe
    xml_resp = doc.get('xml_respuesta_hacienda')
    if xml_resp and str(xml_resp).strip():
        resp_bytes = xml_resp.encode('utf-8') if isinstance(xml_resp, str) else xml_resp
        resp_attach = MIMEApplication(resp_bytes, _subtype="xml")
        resp_name = f"{doc.get('clave_numerica') or doc['id']}_respuesta.xml"
        resp_attach.add_header('Content-Disposition', 'attachment', filename=resp_name)
        msg.attach(resp_attach)

    # 6. Despacho SMTP
    simulated = smtp_cfg.get('simulated') or is_mail_simulation_mode()
    if not simulated:
        host = smtp_cfg.get('host')
        port = int(smtp_cfg.get('port') or 587)
        user = smtp_cfg.get('user') or ''
        pwd = smtp_cfg.get('password') or ''
        tls = bool(smtp_cfg.get('tls', True))

        try:
            if port == 465:
                server = smtplib.SMTP_SSL(host, port, timeout=20)
            else:
                server = smtplib.SMTP(host, port, timeout=20)
                if tls:
                    server.starttls()
            if user and pwd:
                server.login(user, pwd)
            server.sendmail(from_email, [destinatario], msg.as_string())
            server.quit()
        except Exception as e_send:
            # Marcar fallo en base de datos
            with database.get_db(db_path) as conn:
                conn.execute("""
                    UPDATE electronic_documents
                    SET correo_destinatario = ?, correo_estado = 'fallido', updated_at = datetime('now','localtime')
                    WHERE id = ? AND company_id = ?
                """, (destinatario, did, cid))
            raise MailError(f"Error al enviar correo por SMTP: {e_send}")

    # 7. Registrar envío exitoso en base de datos y auditoría
    with database.get_db(db_path) as conn:
        conn.execute("""
            UPDATE electronic_documents
            SET correo_enviado_at = datetime('now','localtime'),
                correo_destinatario = ?,
                correo_estado = 'enviado',
                updated_at = datetime('now','localtime')
            WHERE id = ? AND company_id = ?
        """, (destinatario, did, cid))

        database.insert_audit_log(conn, 'ventas_mail_sent', actor or 'system', 'document', did,
                                  {'destinatario': destinatario, 'asunto': asunto, 'simulado': simulated})

    return {
        'ok': True,
        'destinatario': destinatario,
        'asunto': asunto,
        'simulado': simulated,
        'adjuntos': [pdf_name, xml_name]
    }
