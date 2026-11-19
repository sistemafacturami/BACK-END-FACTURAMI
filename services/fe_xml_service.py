"""
FACTURAMI - Generador Oficial de Comprobantes XML v4.3 y v4.4 para Ministerio de Hacienda (Costa Rica).
=======================================================================================================
Genera los XML estandarizados de:
  - 01: Factura Electrónica
  - 04: Tiquete Electrónico
  - 03: Nota de Crédito Electrónica
  - 02: Nota de Débito Electrónica
  - 08: Factura Electrónica de Exportación
  - 09: Factura Electrónica de Compra
Estructura según esquemas oficiales XSD de la Dirección General de Tributación.
"""
import re
from datetime import datetime
import xml.etree.ElementTree as ET

SCHEMAS = {
    '01': ('FacturaElectronica', 'https://cdn.comprobanteselectronicos.go.cr/xml-schemas/v4.3/facturaElectronica'),
    '04': ('TiqueteElectronico', 'https://cdn.comprobanteselectronicos.go.cr/xml-schemas/v4.3/tiqueteElectronico'),
    '03': ('NotaCreditoElectronica', 'https://cdn.comprobanteselectronicos.go.cr/xml-schemas/v4.3/notaCreditoElectronica'),
    '02': ('NotaDebitoElectronica', 'https://cdn.comprobanteselectronicos.go.cr/xml-schemas/v4.3/notaDebitoElectronica'),
    '08': ('FacturaElectronicaExportacion', 'https://cdn.comprobanteselectronicos.go.cr/xml-schemas/v4.3/facturaElectronicaExportacion'),
    '09': ('FacturaElectronicaCompra', 'https://cdn.comprobanteselectronicos.go.cr/xml-schemas/v4.3/facturaElectronicaCompra'),
}


class XmlGenerationError(ValueError):
    pass


def _fmt_dec(val, decimals=2):
    return f"{float(val or 0.0):.{decimals}f}"


def _add_sub(parent, tag, text):
    if text is not None and str(text).strip() != '':
        el = ET.SubElement(parent, tag)
        el.text = str(text).strip()
        return el
    return None


def generate_electronic_invoice_xml(doc_data: dict, emisor_data: dict, receptor_data: dict = None,
                                    lineas: list = None, referencia: dict = None) -> str:
    """
    Construye el XML oficial de comprobante electrónico sin firmar.
    Retorna el XML como cadena de texto UTF-8 con declaración XML.
    """
    tipo_doc = str(doc_data.get('tipo_documento') or '01').strip()
    if tipo_doc not in SCHEMAS:
        raise XmlGenerationError(f"Tipo de comprobante '{tipo_doc}' no soportado")

    root_tag, ns_url = SCHEMAS[tipo_doc]

    # Atributos del elemento raíz con espacios de nombres de Hacienda
    root = ET.Element(root_tag, {
        'xmlns': ns_url,
        'xmlns:ds': "http://www.w3.org/2000/09/xmldsig#",
        'xmlns:xsd': "http://www.w3.org/2001/XMLSchema",
        'xmlns:xsi': "http://www.w3.org/2001/XMLSchema-instance",
    })

    # 1. Clave
    clave = str(doc_data.get('clave_numerica') or '').strip()
    if len(clave) != 50:
        raise XmlGenerationError(f"La clave numérica debe tener 50 dígitos (recibido: {len(clave)})")
    _add_sub(root, 'Clave', clave)

    # 2. Código Actividad
    actividad = re.sub(r'\D', '', str(emisor_data.get('actividad_principal') or '000000')).zfill(6)[:6]
    _add_sub(root, 'CodigoActividad', actividad)

    # 3. Número Consecutivo
    consecutivo = str(doc_data.get('consecutivo') or '').strip()
    if len(consecutivo) != 20:
        raise XmlGenerationError(f"El consecutivo debe tener 20 dígitos (recibido: {len(consecutivo)})")
    _add_sub(root, 'NumeroConsecutivo', consecutivo)

    # 4. Fecha Emisión (ISO 8601 con zona horaria de Costa Rica -06:00)
    fecha_emision = str(doc_data.get('fecha_emision') or '')
    if not fecha_emision:
        fecha_emision = datetime.now().strftime('%Y-%m-%dT%H:%M:%S-06:00')
    elif len(fecha_emision) == 19 and 'T' in fecha_emision and not ('+' in fecha_emision or '-' in fecha_emision[10:]):
        fecha_emision += '-06:00'
    _add_sub(root, 'FechaEmision', fecha_emision)

    # 5. Emisor
    emisor_el = ET.SubElement(root, 'Emisor')
    nom_emisor = str(emisor_data.get('razon_social') or emisor_data.get('nombre') or '').strip()[:100]
    if not nom_emisor:
        raise XmlGenerationError("Nombre o razón social del emisor es requerido")
    _add_sub(emisor_el, 'Nombre', nom_emisor)

    id_emisor_el = ET.SubElement(emisor_el, 'Identificacion')
    _add_sub(id_emisor_el, 'Tipo', str(emisor_data.get('tipo_identificacion') or '01').strip())
    ced_emisor = re.sub(r'\D', '', str(emisor_data.get('identificacion') or emisor_data.get('id') or ''))
    _add_sub(id_emisor_el, 'Numero', ced_emisor)

    if emisor_data.get('nombre_comercial'):
        _add_sub(emisor_el, 'NombreComercial', str(emisor_data['nombre_comercial'])[:80])

    # Ubicación Emisor
    ub_emisor = ET.SubElement(emisor_el, 'Ubicacion')
    _add_sub(ub_emisor, 'Provincia', str(emisor_data.get('provincia') or '1')[:1])
    _add_sub(ub_emisor, 'Canton', str(emisor_data.get('canton') or '01').zfill(2)[:2])
    _add_sub(ub_emisor, 'Distrito', str(emisor_data.get('distrito') or '01').zfill(2)[:2])
    if emisor_data.get('barrio'):
        _add_sub(ub_emisor, 'Barrio', str(emisor_data['barrio']).zfill(2)[:2])
    _add_sub(ub_emisor, 'OtrasSenas', str(emisor_data.get('otras_senas') or emisor_data.get('direccion') or 'Costa Rica')[:250])

    # Teléfono Emisor
    tel = re.sub(r'\D', '', str(emisor_data.get('telefono') or ''))
    if tel:
        tel_el = ET.SubElement(emisor_el, 'Telefono')
        _add_sub(tel_el, 'CodigoPais', '506')
        _add_sub(tel_el, 'NumTelefono', tel[-8:] if len(tel) >= 8 else tel)

    if emisor_data.get('email'):
        _add_sub(emisor_el, 'CorreoElectronico', str(emisor_data['email'])[:120])

    # 6. Receptor (Obligatorio en Factura 01, NC 03, ND 02; opcional en Tiquete 04)
    if receptor_data and receptor_data.get('nombre'):
        rec_el = ET.SubElement(root, 'Receptor')
        _add_sub(rec_el, 'Nombre', str(receptor_data['nombre'])[:100])

        rec_num = re.sub(r'\D', '', str(receptor_data.get('identificacion') or ''))
        if rec_num:
            id_rec_el = ET.SubElement(rec_el, 'Identificacion')
            _add_sub(id_rec_el, 'Tipo', str(receptor_data.get('tipo_identificacion') or '01').strip())
            _add_sub(id_rec_el, 'Numero', rec_num)

        if receptor_data.get('nombre_comercial'):
            _add_sub(rec_el, 'NombreComercial', str(receptor_data['nombre_comercial'])[:80])

        if receptor_data.get('otras_senas') or receptor_data.get('direccion'):
            ub_rec = ET.SubElement(rec_el, 'Ubicacion')
            _add_sub(ub_rec, 'Provincia', str(receptor_data.get('provincia') or '1')[:1])
            _add_sub(ub_rec, 'Canton', str(receptor_data.get('canton') or '01').zfill(2)[:2])
            _add_sub(ub_rec, 'Distrito', str(receptor_data.get('distrito') or '01').zfill(2)[:2])
            _add_sub(ub_rec, 'OtrasSenas', str(receptor_data.get('otras_senas') or receptor_data.get('direccion'))[:250])

        if receptor_data.get('email'):
            _add_sub(rec_el, 'CorreoElectronico', str(receptor_data['email'])[:120])
    elif tipo_doc in ('01', '02', '03'):
        # En factura oficial, si no hay receptor específico, se emite a cliente genérico
        rec_el = ET.SubElement(root, 'Receptor')
        _add_sub(rec_el, 'Nombre', 'Cliente Receptor Comercial')
        id_rec_el = ET.SubElement(rec_el, 'Identificacion')
        _add_sub(id_rec_el, 'Tipo', '01')
        _add_sub(id_rec_el, 'Numero', '000000000')

    # 7. Condición de Venta y Plazo
    cond_venta = str(doc_data.get('condicion_venta') or '01').strip()
    _add_sub(root, 'CondicionVenta', cond_venta)
    if cond_venta == '06':
        plazo = int(doc_data.get('plazo_credito') or 30)
        _add_sub(root, 'PlazoCredito', str(plazo))

    # 8. Medio de Pago
    medio_pago = str(doc_data.get('medio_pago') or '01').strip()
    _add_sub(root, 'MedioPago', medio_pago)

    # 9. Detalle de Servicio / Mercancías
    lineas = lineas or []
    if not lineas:
        raise XmlGenerationError("El comprobante debe tener al menos una línea de detalle")

    detalle_el = ET.SubElement(root, 'DetalleServicio')

    tot_serv_grav = 0.0
    tot_serv_ex = 0.0
    tot_merc_grav = 0.0
    tot_merc_ex = 0.0
    tot_descuentos = 0.0
    tot_impuestos = 0.0

    for idx, l in enumerate(lineas, 1):
        linea_el = ET.SubElement(detalle_el, 'LineaDetalle')
        _add_sub(linea_el, 'NumeroLinea', str(idx))

        cabys = re.sub(r'\D', '', str(l.get('codigo_cabys') or '0000000000000')).zfill(13)[:13]
        _add_sub(linea_el, 'Codigo', cabys)

        # Código comercial interno
        cod_int = str(l.get('codigo_producto') or l.get('codigo_interno') or f"ITEM-{idx}").strip()[:20]
        cod_com_el = ET.SubElement(linea_el, 'CodigoComercial')
        _add_sub(cod_com_el, 'Tipo', '04')
        _add_sub(cod_com_el, 'Codigo', cod_int)

        cant = max(0.001, float(l.get('cantidad') or 1.0))
        _add_sub(linea_el, 'Cantidad', _fmt_dec(cant, 3))
        _add_sub(linea_el, 'UnidadMedida', str(l.get('unidad_medida') or 'Unid')[:5])
        _add_sub(linea_el, 'Detalle', str(l.get('descripcion') or l.get('nombre') or 'Línea de venta')[:200])

        precio_unit = float(l.get('precio_unitario') or 0.0)
        _add_sub(linea_el, 'PrecioUnitario', _fmt_dec(precio_unit, 5))

        monto_total = round(cant * precio_unit, 2)
        _add_sub(linea_el, 'MontoTotal', _fmt_dec(monto_total, 2))

        # Descuentos
        desc_monto = round(float(l.get('monto_descuento') or 0.0), 2)
        if desc_monto > 0:
            desc_el = ET.SubElement(linea_el, 'Descuento')
            _add_sub(desc_el, 'MontoDescuento', _fmt_dec(desc_monto, 2))
            _add_sub(desc_el, 'NaturalezaDescuento', str(l.get('naturaleza_descuento') or 'Descuento comercial')[:80])
            tot_descuentos += desc_monto

        subtotal_linea = round(monto_total - desc_monto, 2)
        _add_sub(linea_el, 'SubTotal', _fmt_dec(subtotal_linea, 2))
        _add_sub(linea_el, 'BaseImponible', _fmt_dec(subtotal_linea, 2))

        # Impuesto IVA
        iva_cod = str(l.get('tarifa_iva_codigo') or '08').strip()
        iva_pct = float(l.get('tarifa_iva_porcentaje') or (13.0 if iva_cod == '08' else 0.0))
        imp_monto = round(subtotal_linea * (iva_pct / 100.0), 2)

        if iva_pct > 0 or iva_cod != '01':
            imp_el = ET.SubElement(linea_el, 'Impuesto')
            _add_sub(imp_el, 'Codigo', '01')  # 01 = IVA
            _add_sub(imp_el, 'CodigoTarifa', iva_cod)
            _add_sub(imp_el, 'Tarifa', _fmt_dec(iva_pct, 2))
            _add_sub(imp_el, 'Monto', _fmt_dec(imp_monto, 2))
            _add_sub(linea_el, 'ImpuestoNeto', _fmt_dec(imp_monto, 2))
            tot_impuestos += imp_monto
        else:
            _add_sub(linea_el, 'ImpuestoNeto', '0.00')

        tot_linea = round(subtotal_linea + imp_monto, 2)
        _add_sub(linea_el, 'MontoTotalLinea', _fmt_dec(tot_linea, 2))

        # Acumuladores de resumen
        es_servicio = str(l.get('unidad_medida') or '').strip() == 'Sp' or l.get('tipo') == 'servicio'
        if iva_pct > 0:
            if es_servicio:
                tot_serv_grav += subtotal_linea
            else:
                tot_merc_grav += subtotal_linea
        else:
            if es_servicio:
                tot_serv_ex += subtotal_linea
            else:
                tot_merc_ex += subtotal_linea

    # 10. Resumen de la Factura
    resumen_el = ET.SubElement(root, 'ResumenFactura')
    moneda = str(doc_data.get('moneda') or 'CRC').strip().upper()
    tc = float(doc_data.get('tipo_cambio') or 1.0)
    cod_mon_el = ET.SubElement(resumen_el, 'CodigoTipoMoneda')
    _add_sub(cod_mon_el, 'CodigoMoneda', moneda)
    _add_sub(cod_mon_el, 'TipoCambio', _fmt_dec(tc, 2))

    _add_sub(resumen_el, 'TotalServGravados', _fmt_dec(tot_serv_grav, 2))
    _add_sub(resumen_el, 'TotalServExentos', _fmt_dec(tot_serv_ex, 2))
    _add_sub(resumen_el, 'TotalServExonerado', '0.00')
    _add_sub(resumen_el, 'TotalMercanciasGravadas', _fmt_dec(tot_merc_grav, 2))
    _add_sub(resumen_el, 'TotalMercanciasExentas', _fmt_dec(tot_merc_ex, 2))
    _add_sub(resumen_el, 'TotalMercanciasExonerada', '0.00')

    tot_grav = round(tot_serv_grav + tot_merc_grav, 2)
    tot_ex = round(tot_serv_ex + tot_merc_ex, 2)
    tot_venta = round(tot_grav + tot_ex, 2)
    tot_venta_neta = round(tot_venta - tot_descuentos, 2)
    tot_comprobante = round(tot_venta_neta + tot_impuestos, 2)

    _add_sub(resumen_el, 'TotalGravado', _fmt_dec(tot_grav, 2))
    _add_sub(resumen_el, 'TotalExento', _fmt_dec(tot_ex, 2))
    _add_sub(resumen_el, 'TotalExonerado', '0.00')
    _add_sub(resumen_el, 'TotalVenta', _fmt_dec(tot_venta, 2))
    _add_sub(resumen_el, 'TotalDescuentos', _fmt_dec(tot_descuentos, 2))
    _add_sub(resumen_el, 'TotalVentaNeta', _fmt_dec(tot_venta_neta, 2))
    _add_sub(resumen_el, 'TotalImpuesto', _fmt_dec(tot_impuestos, 2))
    _add_sub(resumen_el, 'TotalComprobante', _fmt_dec(tot_comprobante, 2))

    # 11. Información de Referencia (Obligatorio en Notas de Crédito '03' y Débito '02')
    if referencia and referencia.get('numero'):
        ref_el = ET.SubElement(root, 'InformacionReferencia')
        _add_sub(ref_el, 'TipoDoc', str(referencia.get('tipo_documento') or '01')[:2])
        _add_sub(ref_el, 'Numero', str(referencia.get('numero'))[:50])
        ref_fecha = str(referencia.get('fecha_emision') or datetime.now().strftime('%Y-%m-%dT%H:%M:%S-06:00'))
        _add_sub(ref_el, 'FechaEmision', ref_fecha)
        _add_sub(ref_el, 'Codigo', str(referencia.get('codigo') or '01')[:2])
        _add_sub(ref_el, 'Razon', str(referencia.get('razon') or 'Corrección de comprobante electrónico')[:180])

    xml_body = ET.tostring(root, encoding='utf-8').decode('utf-8')
    return '<?xml version="1.0" encoding="utf-8"?>\n' + xml_body
