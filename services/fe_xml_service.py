"""
FACTURAMI - Generador Oficial de Comprobantes XML v4.4 para Ministerio de Hacienda (Costa Rica).
=================================================================================================
Genera los XML estandarizados de:
  - 01: Factura Electrónica
  - 04: Tiquete Electrónico
  - 03: Nota de Crédito Electrónica
  - 02: Nota de Débito Electrónica
  - 08: Factura Electrónica de Exportación
  - 09: Factura Electrónica de Compra
Estructura según esquemas oficiales XSD v4.4 (Resolución MH-DGT-RES-0027-2024).
"""
import re
from datetime import datetime, timezone, timedelta
import xml.etree.ElementTree as ET

SCHEMAS = {
    '01': ('FacturaElectronica', 'https://cdn.comprobanteselectronicos.go.cr/xml-schemas/v4.4/facturaElectronica'),
    '04': ('TiqueteElectronico', 'https://cdn.comprobanteselectronicos.go.cr/xml-schemas/v4.4/tiqueteElectronico'),
    '03': ('NotaCreditoElectronica', 'https://cdn.comprobanteselectronicos.go.cr/xml-schemas/v4.4/notaCreditoElectronica'),
    '02': ('NotaDebitoElectronica', 'https://cdn.comprobanteselectronicos.go.cr/xml-schemas/v4.4/notaDebitoElectronica'),
    '08': ('FacturaElectronicaExportacion', 'https://cdn.comprobanteselectronicos.go.cr/xml-schemas/v4.4/facturaElectronicaExportacion'),
    '09': ('FacturaElectronicaCompra', 'https://cdn.comprobanteselectronicos.go.cr/xml-schemas/v4.4/facturaElectronicaCompra'),
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
    Construye el XML oficial de comprobante electrónico sin firmar conforme al esquema v4.4.
    Retorna el XML como cadena de texto UTF-8 con declaración XML.
    """
    tipo_doc = str(doc_data.get('tipo_documento') or '01').strip()
    if tipo_doc not in SCHEMAS:
        raise XmlGenerationError(f"Tipo de comprobante '{tipo_doc}' no soportado")

    root_tag, ns_url = SCHEMAS[tipo_doc]

    # Atributos del elemento raíz con espacios de nombres de Hacienda v4.4
    root = ET.Element(root_tag, {
        'xmlns': ns_url,
        'xmlns:ds': "http://www.w3.org/2000/09/xmldsig#",
        'xmlns:xsd': "http://www.w3.org/2001/XMLSchema",
        'xmlns:xsi': "http://www.w3.org/2001/XMLSchema-instance",
    })

    # 1. Clave (50 dígitos)
    clave = str(doc_data.get('clave_numerica') or '').strip()
    if len(clave) != 50:
        raise XmlGenerationError(f"La clave numérica debe tener 50 dígitos (recibido: {len(clave)})")
    _add_sub(root, 'Clave', clave)

    # 2. ProveedorSistemas (Obligatorio v4.4: Cédula del emisor / proveedor de software)
    ced_emisor = re.sub(r'\D', '', str(emisor_data.get('identificacion') or emisor_data.get('id') or ''))
    proveedor = str(doc_data.get('proveedor_sistemas') or emisor_data.get('proveedor_sistemas') or ced_emisor or '504470991').strip()
    _add_sub(root, 'ProveedorSistemas', proveedor)

    # 3. Código Actividad Emisor (v4.4: CodigoActividadEmisor)
    actividad = str(emisor_data.get('actividad_principal') or emisor_data.get('actividad') or '6920.0').strip()
    if not actividad:
        actividad = '6920.0'
    _add_sub(root, 'CodigoActividadEmisor', actividad)

    # 4. Código Actividad Receptor (Opcional v4.4)
    act_rec = doc_data.get('codigo_actividad_receptor') or (receptor_data.get('actividad') if receptor_data else None)
    if act_rec:
        _add_sub(root, 'CodigoActividadReceptor', str(act_rec).strip())

    # 5. Número Consecutivo (20 dígitos)
    consecutivo = str(doc_data.get('consecutivo') or '').strip()
    if len(consecutivo) != 20:
        raise XmlGenerationError(f"El consecutivo debe tener 20 dígitos (recibido: {len(consecutivo)})")
    _add_sub(root, 'NumeroConsecutivo', consecutivo)

    # 6. Fecha Emisión (ISO 8601 con zona horaria de Costa Rica -06:00)
    fecha_emision = str(doc_data.get('fecha_emision') or '')
    if not fecha_emision:
        cr_tz = timezone(timedelta(hours=-6))
        fecha_emision = datetime.now(cr_tz).strftime('%Y-%m-%dT%H:%M:%S-06:00')
    elif len(fecha_emision) == 19 and 'T' in fecha_emision and not ('+' in fecha_emision or '-' in fecha_emision[10:]):
        fecha_emision += '-06:00'
    _add_sub(root, 'FechaEmision', fecha_emision)

    # 7. Emisor
    emisor_el = ET.SubElement(root, 'Emisor')
    nom_emisor = str(emisor_data.get('razon_social') or emisor_data.get('nombre') or '').strip()[:100]
    if not nom_emisor:
        raise XmlGenerationError("Nombre o razón social del emisor es requerido")
    _add_sub(emisor_el, 'Nombre', nom_emisor)

    id_emisor_el = ET.SubElement(emisor_el, 'Identificacion')
    _add_sub(id_emisor_el, 'Tipo', str(emisor_data.get('tipo_identificacion') or '01').strip())
    _add_sub(id_emisor_el, 'Numero', ced_emisor)

    if emisor_data.get('nombre_comercial'):
        _add_sub(emisor_el, 'NombreComercial', str(emisor_data['nombre_comercial'])[:80])

    # Ubicación Emisor
    ub_emisor = ET.SubElement(emisor_el, 'Ubicacion')
    _add_sub(ub_emisor, 'Provincia', str(emisor_data.get('provincia') or '5')[:1])
    _add_sub(ub_emisor, 'Canton', str(emisor_data.get('canton') or '01').zfill(2)[:2])
    _add_sub(ub_emisor, 'Distrito', str(emisor_data.get('distrito') or '01').zfill(2)[:2])
    if emisor_data.get('barrio'):
        _add_sub(ub_emisor, 'Barrio', str(emisor_data['barrio']).zfill(2)[:2])
    _add_sub(ub_emisor, 'OtrasSenas', str(emisor_data.get('otras_senas') or emisor_data.get('direccion') or 'Costa Rica')[:250])

    # Teléfono Emisor
    tel = re.sub(r'\D', '', str(emisor_data.get('telefono') or '88888888'))
    if tel:
        tel_el = ET.SubElement(emisor_el, 'Telefono')
        _add_sub(tel_el, 'CodigoPais', '506')
        _add_sub(tel_el, 'NumTelefono', tel[-8:] if len(tel) >= 8 else tel.zfill(8))

    # Correo Electrónico Emisor (Obligatorio en v4.4)
    email_emisor = str(emisor_data.get('email') or emisor_data.get('correo') or 'lvarmedi04@gmail.com').strip()[:120]
    _add_sub(emisor_el, 'CorreoElectronico', email_emisor)

    # 8. Receptor (Obligatorio en Factura 01, NC 03, ND 02; opcional en Tiquete 04)
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
        rec_el = ET.SubElement(root, 'Receptor')
        _add_sub(rec_el, 'Nombre', 'Cliente Receptor Comercial')
        id_rec_el = ET.SubElement(rec_el, 'Identificacion')
        _add_sub(id_rec_el, 'Tipo', '01')
        _add_sub(id_rec_el, 'Numero', ced_emisor if ced_emisor else '000000000')

    # 9. Condición de Venta y Plazo
    cond_venta = str(doc_data.get('condicion_venta') or '01').strip()
    _add_sub(root, 'CondicionVenta', cond_venta)
    if cond_venta in ('02', '06', '08', '10') or doc_data.get('plazo_credito'):
        plazo = int(doc_data.get('plazo_credito') or 30)
        _add_sub(root, 'PlazoCredito', str(plazo))

    # NOTA: En v4.4, MedioPago se traslada adentro de ResumenFactura (antes de TotalComprobante).

    # 10. Detalle de Servicio / Mercancías
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

        cabys = re.sub(r'\D', '', str(l.get('codigo_cabys') or '8221000000000')).zfill(13)[:13]
        if cabys == '0000000000000':
            cabys = '8221000000000'
        _add_sub(linea_el, 'CodigoCABYS', cabys)

        # Código comercial interno
        cod_int = str(l.get('codigo_producto') or l.get('codigo_interno') or f"SERV-{idx}").strip()[:20]
        cod_com_el = ET.SubElement(linea_el, 'CodigoComercial')
        _add_sub(cod_com_el, 'Tipo', '04')
        _add_sub(cod_com_el, 'Codigo', cod_int)

        cant = max(0.001, float(l.get('cantidad') or 1.0))
        _add_sub(linea_el, 'Cantidad', _fmt_dec(cant, 3))
        _add_sub(linea_el, 'UnidadMedida', str(l.get('unidad_medida') or 'Sp')[:5])
        _add_sub(linea_el, 'Detalle', str(l.get('descripcion') or l.get('nombre') or 'Línea de servicio/venta')[:200])

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
            _add_sub(imp_el, 'CodigoTarifaIVA', iva_cod)
            _add_sub(imp_el, 'Tarifa', _fmt_dec(iva_pct, 2))
            _add_sub(imp_el, 'Monto', _fmt_dec(imp_monto, 2))
            tot_impuestos += imp_monto

        # v4.4: ImpuestoAsumidoEmisorFabrica obligatorio en LineaDetalle
        _add_sub(linea_el, 'ImpuestoAsumidoEmisorFabrica', '0.00000')
        _add_sub(linea_el, 'ImpuestoNeto', _fmt_dec(imp_monto if iva_pct > 0 else 0.0, 2))

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

    # 11. Resumen de la Factura (v4.4)
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
    _add_sub(resumen_el, 'TotalMercExonerada', '0.00')

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

    # TotalDesgloseImpuesto (Obligatorio en v4.4 si hay impuesto)
    if tot_impuestos > 0:
        desglose_el = ET.SubElement(resumen_el, 'TotalDesgloseImpuesto')
        _add_sub(desglose_el, 'Codigo', '01')
        _add_sub(desglose_el, 'CodigoTarifaIVA', '08')
        _add_sub(desglose_el, 'TotalMontoImpuesto', _fmt_dec(tot_impuestos, 2))

    _add_sub(resumen_el, 'TotalImpuesto', _fmt_dec(tot_impuestos, 2))

    # MedioPago en v4.4 (Ubicado adentro de ResumenFactura, justo antes de TotalComprobante)
    if cond_venta not in ('02', '08', '10'):
        medio_pago = str(doc_data.get('medio_pago') or '01').strip()
        mp_el = ET.SubElement(resumen_el, 'MedioPago')
        _add_sub(mp_el, 'TipoMedioPago', medio_pago)
        _add_sub(mp_el, 'TotalMedioPago', _fmt_dec(tot_comprobante, 2))

    _add_sub(resumen_el, 'TotalComprobante', _fmt_dec(tot_comprobante, 2))

    # 12. Información de Referencia (Obligatorio en Notas de Crédito '03' y Débito '02')
    if referencia and referencia.get('numero'):
        ref_el = ET.SubElement(root, 'InformacionReferencia')
        _add_sub(ref_el, 'TipoDocIR', str(referencia.get('tipo_documento') or '01')[:2])
        _add_sub(ref_el, 'Numero', str(referencia.get('numero'))[:50])
        ref_fecha = str(referencia.get('fecha_emision') or datetime.now(timezone(timedelta(hours=-6))).strftime('%Y-%m-%dT%H:%M:%S-06:00'))
        _add_sub(ref_el, 'FechaEmisionIR', ref_fecha)
        _add_sub(ref_el, 'Codigo', str(referencia.get('codigo') or '01')[:2])
        _add_sub(ref_el, 'Razon', str(referencia.get('razon') or 'Corrección de comprobante electrónico')[:180])

    xml_body = ET.tostring(root, encoding='utf-8').decode('utf-8')
    return '<?xml version="1.0" encoding="utf-8"?>\n' + xml_body
