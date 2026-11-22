"""
FACTURAMI - Generador Oficial de Consecutivos y Clave Numérica de Hacienda Costa Rica.
=====================================================================================
Regulaciones DGT-R-48-2016 / DGT-R-033-2019 / Anexos y Estructuras v4.3 y v4.4:
  - Consecutivo (20 dígitos): SSS (3) + TTTTT (5) + CC (2) + NNNNNNNNNN (10)
  - Clave Numérica (50 dígitos): 506 (3) + DD (2) + MM (2) + YY (2) + CÉDULA (12) +
                                 CONSECUTIVO (20) + SITUACIÓN (1) + SEGURIDAD (8)
"""
import random
import re
from datetime import datetime

TIPOS_COMPROBANTE = {
    '01': 'Factura Electrónica',
    '02': 'Nota de Débito Electrónica',
    '03': 'Nota de Crédito Electrónica',
    '04': 'Tiquete Electrónico',
    '08': 'Factura Electrónica de Exportación',
    '09': 'Factura Electrónica de Compra',
}

SITUACIONES = {
    '1': 'Normal',
    '2': 'Contingencia',
    '3': 'Sin internet',
}


class ClaveError(ValueError):
    pass


def generar_consecutivo(sucursal: str, terminal: str, tipo_doc: str, numero: int) -> str:
    """
    Genera el número consecutivo oficial de 20 dígitos:
    - Sucursal: 3 dígitos numéricos
    - Terminal: 5 dígitos numéricos
    - Tipo Documento: 2 dígitos ('01', '02', '03', '04', '08', '09')
    - Número secuencial: 10 dígitos numéricos
    """
    s = re.sub(r'\D', '', str(sucursal or '1')).zfill(3)[:3]
    t = re.sub(r'\D', '', str(terminal or '1')).zfill(5)[:5]
    td = str(tipo_doc or '01').strip()
    if td not in TIPOS_COMPROBANTE:
        raise ClaveError(f"Tipo de comprobante inválido: '{td}'")

    try:
        n = int(numero)
        if n < 0 or n > 9999999999:
            raise ClaveError("El número consecutivo debe estar entre 1 y 9999999999")
    except (ValueError, TypeError):
        raise ClaveError("El número consecutivo debe ser entero")

    num_str = f"{n:010d}"
    consecutivo = f"{s}{t}{td}{num_str}"
    if len(consecutivo) != 20:
        raise ClaveError("El consecutivo generado no tiene 20 dígitos")
    return consecutivo


def generar_clave(cedula_emisor: str, consecutivo: str, fecha: datetime = None,
                  situacion: str = '1', codigo_seguridad: str = None) -> str:
    """
    Genera la clave numérica de 50 dígitos para el Ministerio de Hacienda:
    - Código país: 506 (3 dígitos)
    - Día: 2 dígitos
    - Mes: 2 dígitos
    - Año: 2 dígitos (ej: 26 para 2026)
    - Cédula emisor: 12 dígitos (ceros a la izquierda)
    - Consecutivo: 20 dígitos
    - Situación: 1 (Normal), 2 (Contingencia), 3 (Sin internet)
    - Código de seguridad: 8 dígitos numéricos
    """
    if not fecha:
        fecha = datetime.now()
    elif isinstance(fecha, str):
        try:
            fecha = datetime.fromisoformat(fecha)
        except Exception:
            try:
                fecha = datetime.strptime(fecha[:10], '%Y-%m-%d')
            except Exception:
                fecha = datetime.now()

    ced_clean = re.sub(r'\D', '', str(cedula_emisor or ''))
    if not ced_clean:
        raise ClaveError("Cédula de emisor es requerida")
    ced_12 = ced_clean.zfill(12)
    if len(ced_12) > 12:
        ced_12 = ced_12[-12:]

    c_clean = re.sub(r'\D', '', str(consecutivo or ''))
    if len(c_clean) != 20:
        raise ClaveError(f"El consecutivo debe tener exactamente 20 dígitos (recibido {len(c_clean)})")

    sit = str(situacion or '1').strip()
    if sit not in SITUACIONES:
        raise ClaveError(f"Situación del comprobante inválida: '{sit}'")

    if codigo_seguridad:
        cod_sec = re.sub(r'\D', '', str(codigo_seguridad)).zfill(8)[:8]
    else:
        cod_sec = f"{random.randint(10000000, 99999999):08d}"

    pais = "506"
    dia = f"{fecha.day:02d}"
    mes = f"{fecha.month:02d}"
    anio = f"{fecha.year % 100:02d}"

    clave = f"{pais}{dia}{mes}{anio}{ced_12}{c_clean}{sit}{cod_sec}"
    if len(clave) != 50 or not clave.isdigit():
        raise ClaveError("La clave generada no cumple con los 50 dígitos numéricos exactos")
    return clave


def desglosar_clave(clave: str) -> dict:
    """Desglosa una clave de 50 dígitos en sus campos constitutivos oficiales."""
    c = str(clave or '').strip()
    if len(c) != 50 or not c.isdigit():
        raise ClaveError("La clave debe tener exactamente 50 dígitos numéricos")

    return {
        'pais': c[0:3],
        'dia': c[3:5],
        'mes': c[5:7],
        'anio': '20' + c[7:9],
        'cedula_emisor': c[9:21].lstrip('0'),
        'consecutivo': c[21:41],
        'sucursal': c[21:24],
        'terminal': c[24:29],
        'tipo_documento': c[29:31],
        'tipo_documento_nombre': TIPOS_COMPROBANTE.get(c[29:31], 'Desconocido'),
        'numero_comprobante': int(c[31:41]),
        'situacion': c[41],
        'situacion_nombre': SITUACIONES.get(c[41], 'Desconocida'),
        'codigo_seguridad': c[42:50],
    }
