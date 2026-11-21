"""
FACTURAMI - Comunicación con el API del Ministerio de Hacienda de Costa Rica.
=============================================================================
Endpoints oficiales para Sandbox (STAG) y Producción (PROD):
  - Autenticación OAuth2 (IDP Keycloak de Hacienda)
  - Recepción de Comprobantes Electrónicos (POST /recepcion)
  - Consulta de Estado de Comprobantes (GET /recepcion/{clave})
Soporta simulación controlada para pruebas unitarias sin conexión de red.
"""
import base64
import os
import xml.etree.ElementTree as ET
import requests

HACIENDA_URLS = {
    'stag': {
        'idp': 'https://idp.comprobanteselectronicos.go.cr/auth/realms/rut-stag/protocol/openid-connect/token',
        'api': 'https://api-sandbox.comprobanteselectronicos.go.cr/recepcion/v1/recepcion',
        'client_id': 'api-stag'
    },
    'prod': {
        'idp': 'https://idp.comprobanteselectronicos.go.cr/auth/realms/rut/protocol/openid-connect/token',
        'api': 'https://api.comprobanteselectronicos.go.cr/recepcion/v1/recepcion',
        'client_id': 'api-prod'
    }
}


class HaciendaApiError(Exception):
    pass


def is_simulation_mode() -> bool:
    """Modo simulación activo en pruebas unitarias o cuando no hay credenciales activas."""
    return os.environ.get('FACTURAMI_FE_SIMULATE_HACIENDA', '').lower() in ('1', 'true', 'yes')


def get_hacienda_token(ambiente: str, username: str, password: str, timeout: int = 15) -> str:
    """Obtiene un token Bearer OAuth2 del IDP de Hacienda."""
    if is_simulation_mode():
        return "mock-hacienda-bearer-token-12345"

    amb = 'prod' if str(ambiente).lower() == 'prod' else 'stag'
    cfg = HACIENDA_URLS[amb]

    data = {
        'client_id': cfg['client_id'],
        'username': username,
        'password': password,
        'grant_type': 'password',
    }

    try:
        r = requests.post(cfg['idp'], data=data, timeout=timeout)
        if r.status_code != 200:
            raise HaciendaApiError(f"Error de autenticación con Hacienda ({r.status_code}): {r.text[:200]}")
        return r.json().get('access_token')
    except requests.RequestException as e:
        raise HaciendaApiError(f"No se pudo conectar al servidor de autenticación de Hacienda: {e}")


def send_to_hacienda(ambiente: str, token: str, clave: str, fecha_emision: str,
                     emisor_tipo: str, emisor_id: str,
                     receptor_tipo: str = None, receptor_id: str = None,
                     xml_firmado: str = None, timeout: int = 20) -> dict:
    """
    Envía un comprobante electrónico firmado al API de recepción de Hacienda.
    Hacienda responde HTTP 202 Accepted.
    """
    if is_simulation_mode():
        return {
            'status_code': 202,
            'estado': 'enviado',
            'mensaje': 'Comprobante recibido satisfactoriamente por Hacienda (Simulado)',
            'clave': clave
        }

    amb = 'prod' if str(ambiente).lower() == 'prod' else 'stag'
    api_url = HACIENDA_URLS[amb]['api']

    xml_b64 = base64.b64encode(xml_firmado.encode('utf-8') if isinstance(xml_firmado, str) else xml_firmado).decode('ascii')

    payload = {
        'clave': clave,
        'fecha': fecha_emision,
        'emisor': {
            'tipoIdentificacion': emisor_tipo,
            'numeroIdentificacion': emisor_id
        },
        'comprobanteXml': xml_b64
    }

    if receptor_id:
        payload['receptor'] = {
            'tipoIdentificacion': receptor_tipo or '01',
            'numeroIdentificacion': receptor_id
        }

    headers = {
        'Authorization': f"Bearer {token}",
        'Content-Type': 'application/json'
    }

    try:
        r = requests.post(api_url, json=payload, headers=headers, timeout=timeout)
        if r.status_code in (200, 202):
            return {
                'status_code': r.status_code,
                'estado': 'enviado',
                'mensaje': 'Comprobante recibido para procesamiento en Hacienda',
                'clave': clave
            }
        else:
            raise HaciendaApiError(f"Hacienda rechazó el envío (HTTP {r.status_code}): {r.text[:300]}")
    except requests.RequestException as e:
        raise HaciendaApiError(f"Error de comunicación al enviar a Hacienda: {e}")


def check_hacienda_status(ambiente: str, token: str, clave: str, timeout: int = 15) -> dict:
    """
    Consulta el estado de validación de un comprobante ante el Ministerio de Hacienda.
    Retorna el estado ('procesando', 'aceptado', 'rechazado') y el XML de respuesta decodificado.
    """
    if is_simulation_mode():
        # Generar XML de aceptación oficial simulado
        sim_xml = (
            f'<?xml version="1.0" encoding="utf-8"?>\n'
            f'<MensajeHacienda xmlns="https://cdn.comprobanteselectronicos.go.cr/xml-schemas/v4.3/mensajeHacienda">\n'
            f'  <Clave>{clave}</Clave>\n'
            f'  <NombreEmisor>Emisor Ficticio</NombreEmisor>\n'
            f'  <Mensaje>1</Mensaje>\n'
            f'  <DetalleMensaje>Comprobante aceptado por la Dirección General de Tributación</DetalleMensaje>\n'
            f'  <MontoTotalImpuesto>0.00</MontoTotalImpuesto>\n'
            f'  <MontoTotalFactura>0.00</MontoTotalFactura>\n'
            f'</MensajeHacienda>'
        )
        return {
            'estado': 'aceptado',
            'mensaje': 'Comprobante aceptado por la Dirección General de Tributación',
            'xml_respuesta': sim_xml,
            'clave': clave
        }

    amb = 'prod' if str(ambiente).lower() == 'prod' else 'stag'
    url = f"{HACIENDA_URLS[amb]['api']}/{clave}"

    headers = {'Authorization': f"Bearer {token}"}

    try:
        r = requests.get(url, headers=headers, timeout=timeout)
        if r.status_code == 200:
            data = r.json()
            ind_estado = str(data.get('ind-estado') or '').strip().lower()
            xml_resp_b64 = data.get('respuesta-xml') or ''
            xml_resp = ''
            if xml_resp_b64:
                try:
                    xml_resp = base64.b64decode(xml_resp_b64).decode('utf-8')
                except Exception:
                    xml_resp = xml_resp_b64

            return {
                'estado': ind_estado,
                'mensaje': ind_estado.capitalize(),
                'xml_respuesta': xml_resp,
                'clave': clave
            }
        elif r.status_code == 404:
            return {'estado': 'procesando', 'mensaje': 'Comprobante aún en cola de validación en Hacienda', 'clave': clave}
        else:
            raise HaciendaApiError(f"Error consultando comprobante en Hacienda (HTTP {r.status_code}): {r.text[:200]}")
    except requests.RequestException as e:
        raise HaciendaApiError(f"Error de comunicación al consultar Hacienda: {e}")
