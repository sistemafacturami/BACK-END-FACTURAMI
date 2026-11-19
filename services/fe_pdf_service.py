"""
FACTURAMI - Generador Oficial de Representación Gráfica (PDF) para Comprobantes Electrónicos.
=============================================================================================
Construye la representación gráfica en PDF con código QR y especificaciones oficiales
de la Dirección General de Tributación de Costa Rica (Resolución DGT-R-48-2016).
"""
import io
import qrcode
from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image

from services.fe_clave_service import TIPOS_COMPROBANTE, SITUACIONES


def _fmt(val):
    return f"₡{float(val or 0.0):,.2f}".replace(',', ' ')


def generate_electronic_document_pdf(doc_data: dict, emisor_data: dict, receptor_data: dict = None,
                                     lineas: list = None, referencia: dict = None) -> bytes:
    """
    Genera el archivo PDF en memoria y retorna los bytes crudos.
    """
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=letter,
        leftMargin=36,
        rightMargin=36,
        topMargin=36,
        bottomMargin=36
    )

    styles = getSampleStyleSheet()

    # Estilos tipográficos
    title_style = ParagraphStyle(
        'DocTitle',
        parent=styles['Heading2'],
        fontName='Helvetica-Bold',
        fontSize=13,
        leading=16,
        textColor=colors.HexColor('#1e3a5f')
    )
    subtitle_style = ParagraphStyle(
        'DocSub',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=8.5,
        leading=11,
        textColor=colors.HexColor('#4b5563')
    )
    bold_sub = ParagraphStyle(
        'BoldSub',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=8.5,
        leading=11,
        textColor=colors.HexColor('#1f2937')
    )
    mono_style = ParagraphStyle(
        'DocMono',
        parent=styles['Normal'],
        fontName='Courier',
        fontSize=8,
        leading=10,
        textColor=colors.HexColor('#111827')
    )
    th_style = ParagraphStyle(
        'TH',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=7.5,
        leading=9,
        textColor=colors.HexColor('#ffffff'),
        alignment=1  # Center
    )
    td_style = ParagraphStyle(
        'TD',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=8,
        leading=10,
        textColor=colors.HexColor('#1f2937')
    )
    td_num_style = ParagraphStyle(
        'TDNum',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=8,
        leading=10,
        textColor=colors.HexColor('#1f2937'),
        alignment=2  # Right
    )

    story = []

    # 1. ENCABEZADO: Emisor a la izquierda, Datos del Comprobante a la derecha
    tipo_cod = str(doc_data.get('tipo_documento') or '01')
    tipo_nom = TIPOS_COMPROBANTE.get(tipo_cod, 'COMPROBANTE ELECTRÓNICO').upper()

    nom_emisor = emisor_data.get('razon_social') or emisor_data.get('nombre') or 'EMISOR'
    nom_comercial = emisor_data.get('nombre_comercial') or ''
    ced_emisor = emisor_data.get('identificacion') or emisor_data.get('id') or ''
    tel_emisor = emisor_data.get('telefono') or ''
    mail_emisor = emisor_data.get('email') or ''
    dir_emisor = emisor_data.get('otras_senas') or emisor_data.get('direccion') or 'Costa Rica'

    emisor_html = f"<b>{nom_emisor}</b><br/>"
    if nom_comercial:
        emisor_html += f"<i>{nom_comercial}</i><br/>"
    emisor_html += f"<b>Cédula:</b> {ced_emisor}<br/>"
    if tel_emisor:
        emisor_html += f"<b>Tel:</b> {tel_emisor} &nbsp;&nbsp; "
    if mail_emisor:
        emisor_html += f"<b>Email:</b> {mail_emisor}<br/>"
    emisor_html += f"<b>Ubicación:</b> {dir_emisor}"

    consecutivo = doc_data.get('consecutivo') or ''
    clave = doc_data.get('clave_numerica') or ''
    fecha = doc_data.get('fecha_emision') or ''
    cond_venta = doc_data.get('condicion_venta') or '01'
    cond_label = 'Crédito' if cond_venta == '06' else 'Contado'

    doc_meta_html = f"<font color='#0d9488'><b>{tipo_nom}</b></font><br/>"
    doc_meta_html += f"<b>Consecutivo:</b> {consecutivo}<br/>"
    doc_meta_html += f"<b>Fecha:</b> {fecha}<br/>"
    doc_meta_html += f"<b>Condición:</b> {cond_label} &nbsp;&nbsp; <b>Moneda:</b> {doc_data.get('moneda', 'CRC')}<br/>"
    doc_meta_html += f"<b>Sucursal/Terminal:</b> {doc_data.get('codigo_sucursal', '001')} / {doc_data.get('codigo_terminal', '00001')}"

    header_table = Table(
        [
            [Paragraph(emisor_html, subtitle_style), Paragraph(doc_meta_html, subtitle_style)]
        ],
        colWidths=[310, 230]
    )
    header_table.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
    ]))
    story.append(header_table)

    # Clave de 50 dígitos en caja destacada
    clave_box = Table(
        [
            [Paragraph(f"<b>CLAVE NUMÉRICA DE 50 DÍGITOS:</b> <font face='Courier'>{clave}</font>", subtitle_style)]
        ],
        colWidths=[540]
    )
    clave_box.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#f8fafc')),
        ('BOX', (0, 0), (-1, -1), 0.5, colors.HexColor('#cbd5e1')),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('LEFTPADDING', (0, 0), (-1, -1), 8),
        ('RIGHTPADDING', (0, 0), (-1, -1), 8),
    ]))
    story.append(clave_box)
    story.append(Spacer(1, 10))

    # 2. DATOS DEL RECEPTOR (CLIENTE)
    if receptor_data and receptor_data.get('nombre'):
        rec_nom = receptor_data.get('nombre')
        rec_id = receptor_data.get('identificacion') or '—'
        rec_mail = receptor_data.get('email') or '—'
        rec_tel = receptor_data.get('telefono') or '—'
        rec_dir = receptor_data.get('otras_senas') or receptor_data.get('direccion') or '—'

        rec_html = f"<b>RECEPTOR / CLIENTE:</b> {rec_nom} &nbsp;|&nbsp; <b>Identificación:</b> {rec_id}<br/>"
        rec_html += f"<b>Correo:</b> {rec_mail} &nbsp;&nbsp; <b>Teléfono:</b> {rec_tel} &nbsp;&nbsp; <b>Dirección:</b> {rec_dir}"
        rec_table = Table([[Paragraph(rec_html, subtitle_style)]], colWidths=[540])
        rec_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#f1f5f9')),
            ('BOX', (0, 0), (-1, -1), 0.5, colors.HexColor('#e2e8f0')),
            ('PADDING', (0, 0), (-1, -1), 6),
        ]))
        story.append(rec_table)
        story.append(Spacer(1, 10))

    # Información de referencia si es Nota de Crédito / Débito
    if referencia and referencia.get('numero'):
        ref_html = f"<b>DOCUMENTO DE REFERENCIA:</b> Tipo {referencia.get('tipo_documento', '01')} &nbsp;|&nbsp; "
        ref_html += f"<b>Clave / Número:</b> <font face='Courier'>{referencia.get('numero')}</font><br/>"
        ref_html += f"<b>Razón:</b> {referencia.get('razon', 'Corrección contable')}"
        ref_table = Table([[Paragraph(ref_html, subtitle_style)]], colWidths=[540])
        ref_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#fef3c7')),
            ('BOX', (0, 0), (-1, -1), 0.5, colors.HexColor('#fde68a')),
            ('PADDING', (0, 0), (-1, -1), 6),
        ]))
        story.append(ref_table)
        story.append(Spacer(1, 10))

    # 3. TABLA DE LÍNEAS DE PRODUCTOS / SERVICIOS
    table_data = [
        [
            Paragraph("Lín", th_style),
            Paragraph("Código / CABYS", th_style),
            Paragraph("Descripción", th_style),
            Paragraph("Cant.", th_style),
            Paragraph("Precio Unit.", th_style),
            Paragraph("Desc.", th_style),
            Paragraph("IVA", th_style),
            Paragraph("Total", th_style),
        ]
    ]

    lineas = lineas or []
    for idx, l in enumerate(lineas, 1):
        cod_cabys = f"{l.get('codigo_producto', '')}<br/><font color='#6b7280' size='6'>{l.get('codigo_cabys', '')}</font>"
        cant = float(l.get('cantidad') or 1)
        pu = float(l.get('precio_unitario') or 0)
        desc = float(l.get('monto_descuento') or 0)
        iva_pct = float(l.get('tarifa_iva_porcentaje') or 0)
        tot = float(l.get('total_linea') or 0)

        table_data.append([
            Paragraph(str(idx), td_style),
            Paragraph(cod_cabys, td_style),
            Paragraph(str(l.get('descripcion') or 'Línea'), td_style),
            Paragraph(f"{cant:g} {l.get('unidad_medida', 'Unid')}", td_num_style),
            Paragraph(_fmt(pu), td_num_style),
            Paragraph(_fmt(desc) if desc > 0 else '—', td_num_style),
            Paragraph(f"{iva_pct:g}%", td_style),
            Paragraph(_fmt(tot), td_num_style),
        ])

    items_table = Table(
        table_data,
        colWidths=[24, 75, 175, 55, 65, 45, 35, 66]
    )
    items_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1e3a5f')),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#e2e8f0')),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.HexColor('#ffffff'), colors.HexColor('#f8fafc')]),
    ]))
    story.append(items_table)
    story.append(Spacer(1, 12))

    # 4. RESUMEN FINANCIERO Y CÓDIGO QR
    # Generar imagen de código QR en memoria
    qr_data = f"https://facturami.app/c/{clave}" if clave else "FACTURAMI"
    qr_img = qrcode.make(qr_data)
    qr_io = io.BytesIO()
    qr_img.save(qr_io, format='PNG')
    qr_io.seek(0)
    qr_flowable = Image(qr_io, width=85, height=85)

    subtotal = float(doc_data.get('subtotal') or 0)
    descuentos = float(doc_data.get('total_descuentos') or 0)
    impuestos = float(doc_data.get('total_impuestos') or 0)
    total = float(doc_data.get('total_comprobante') or 0)

    summary_rows = [
        [Paragraph("Subtotal:", bold_sub), Paragraph(_fmt(subtotal), td_num_style)],
        [Paragraph("Total Descuentos:", bold_sub), Paragraph(_fmt(descuentos), td_num_style)],
        [Paragraph("Impuesto al Valor Agregado (IVA):", bold_sub), Paragraph(_fmt(impuestos), td_num_style)],
        [Paragraph("<b>TOTAL COMPROBANTE:</b>", title_style), Paragraph(f"<b>{_fmt(total)}</b>", title_style)],
    ]

    summary_table = Table(summary_rows, colWidths=[200, 140])
    summary_table.setStyle(TableStyle([
        ('LINEBELOW', (0, -1), (-1, -1), 1.5, colors.HexColor('#1e3a5f')),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
        ('TOPPADDING', (0, 0), (-1, -1), 3),
    ]))

    footer_layout = Table(
        [
            [qr_flowable, summary_table]
        ],
        colWidths=[100, 440]
    )
    footer_layout.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
    ]))
    story.append(footer_layout)
    story.append(Spacer(1, 14))

    # 5. RESOLUCIÓN DE HACIENDA Y LEYENDA LEGAL
    res_text = (
        "<b>Autorizada mediante resolución de comprobantes electrónicos N° DGT-R-48-2016 del 7 de octubre de 2016 "
        "y DGT-R-033-2019 de la Dirección General de Tributación de Costa Rica.</b><br/>"
        "Este documento constituye una representación gráfica de la factura electrónica emitida y firmada digitalmente."
    )
    res_para = Paragraph(res_text, subtitle_style)
    story.append(res_para)

    doc.build(story)
    return buf.getvalue()
