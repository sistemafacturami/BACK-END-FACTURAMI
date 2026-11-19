"""
FACTURAMI - Firmado Digital de Comprobantes Electrónicos (XAdES-BES).
====================================================================
Estándar oficial del Ministerio de Hacienda de Costa Rica (DGT).
Utiliza el certificado criptográfico en formato PKCS#12 (.p12) y su PIN,
descifrados en memoria desde `secret_store` (nunca se guardan en texto plano en disco).
Produce una firma digital XML Enveloped estándar XAdES-BES (ETSI TS 101 903).
"""
import base64
import hashlib
import re
import uuid
from datetime import datetime, timezone
import xml.etree.ElementTree as ET

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.serialization import pkcs12

C14N_ALGO = "http://www.w3.org/TR/2001/REC-xml-c14n-20010315"
SIG_ALGO = "http://www.w3.org/2001/04/xmldsig-more#rsa-sha256"
DIGEST_ALGO = "http://www.w3.org/2001/04/xmlenc#sha256"
ENVELOPED_SIG = "http://www.w3.org/2000/09/xmldsig#enveloped-signature"
XADES_NS = "http://uri.etsi.org/01903/v1.3.2#"
DS_NS = "http://www.w3.org/2000/09/xmldsig#"

ET.register_namespace('ds', DS_NS)
ET.register_namespace('xades', XADES_NS)


class SignerError(ValueError):
    pass


def load_p12_credentials(p12_bytes: bytes, pin: str):
    """Carga y valida la llave privada y certificado X.509 desde bytes .p12."""
    if not p12_bytes or not pin:
        raise SignerError("Certificado .p12 y PIN son requeridos para firmar digitalmente")
    try:
        key, cert, _additional = pkcs12.load_key_and_certificates(p12_bytes, pin.encode('utf-8'))
    except Exception as e:
        raise SignerError(f"No se pudo abrir el certificado .p12 con el PIN proporcionado: {e}")

    if not key or not cert:
        raise SignerError("El archivo .p12 no contiene llave privada y certificado válidos")
    return key, cert


def _c14n(element_or_xml) -> bytes:
    """Canonicaliza un elemento o XML string según W3C C14N."""
    if isinstance(element_or_xml, ET.Element):
        xml_str = ET.tostring(element_or_xml, encoding='utf-8').decode('utf-8')
    else:
        xml_str = str(element_or_xml)
    return ET.canonicalize(xml_str).encode('utf-8')


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode('ascii')


def sign_xml_xades_bes(xml_content: str, p12_bytes: bytes, pin: str) -> str:
    """
    Firma un comprobante electrónico XML con estándar XAdES-BES de Costa Rica.
    Inserta el bloque <ds:Signature> al final del elemento raíz preservando
    los prefijos oficiales de Hacienda y W3C XMLDSig.
    """
    key, cert = load_p12_credentials(p12_bytes, pin)

    if isinstance(xml_content, bytes):
        xml_content = xml_content.decode('utf-8')

    # Remover cualquier firma previa si existiera
    clean_xml = re.sub(r'<ds:Signature.*?</ds:Signature>', '', xml_content, flags=re.DOTALL).strip()
    try:
        root = ET.fromstring(clean_xml.encode('utf-8'))
    except Exception as e:
        raise SignerError(f"Error parseando XML para firma: {e}")

    # Identificadores únicos para los nodos de firma
    uid = uuid.uuid4().hex[:12]
    sig_id = f"Signature-{uid}"
    sig_val_id = f"SignatureValue-{uid}"
    key_info_id = f"KeyInfo-{uid}"
    signed_props_id = f"SignedProperties-{uid}"

    # 1. Digest del documento raíz (Enveloped Signature: raíz canonizada sin ds:Signature)
    doc_c14n = _c14n(clean_xml)
    doc_digest = _b64(hashlib.sha256(doc_c14n).digest())

    # 2. Metadatos del certificado del emisor
    der_cert = cert.public_bytes(hashes.serialization.Encoding.DER) if hasattr(hashes, 'serialization') else cert.public_bytes(ET.sys.modules['cryptography.hazmat.primitives.serialization'].Encoding.DER)
    cert_digest = _b64(hashlib.sha256(der_cert).digest())
    cert_b64 = _b64(der_cert)
    signing_time = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    issuer_str = cert.issuer.rfc4514_string()
    serial_str = str(cert.serial_number)

    # 3. Construir <xades:SignedProperties>
    signed_props_xml = (
        f'<xades:SignedProperties xmlns:xades="{XADES_NS}" xmlns:ds="{DS_NS}" Id="{signed_props_id}">'
        f'<xades:SignedSignatureProperties>'
        f'<xades:SigningTime>{signing_time}</xades:SigningTime>'
        f'<xades:SigningCertificate>'
        f'<xades:Cert>'
        f'<xades:CertDigest>'
        f'<ds:DigestMethod Algorithm="{DIGEST_ALGO}"/>'
        f'<ds:DigestValue>{cert_digest}</ds:DigestValue>'
        f'</xades:CertDigest>'
        f'<xades:IssuerSerial>'
        f'<ds:X509IssuerName>{issuer_str}</ds:X509IssuerName>'
        f'<ds:X509SerialNumber>{serial_str}</ds:X509SerialNumber>'
        f'</xades:IssuerSerial>'
        f'</xades:Cert>'
        f'</xades:SigningCertificate>'
        f'</xades:SignedSignatureProperties>'
        f'</xades:SignedProperties>'
    )
    signed_props_c14n = _c14n(signed_props_xml)
    signed_props_digest = _b64(hashlib.sha256(signed_props_c14n).digest())

    # 4. Construir <ds:SignedInfo>
    signed_info_xml = (
        f'<ds:SignedInfo xmlns:ds="{DS_NS}">'
        f'<ds:CanonicalizationMethod Algorithm="{C14N_ALGO}"/>'
        f'<ds:SignatureMethod Algorithm="{SIG_ALGO}"/>'
        f'<ds:Reference Id="Reference-Document" URI="">'
        f'<ds:Transforms>'
        f'<ds:Transform Algorithm="{ENVELOPED_SIG}"/>'
        f'<ds:Transform Algorithm="{C14N_ALGO}"/>'
        f'</ds:Transforms>'
        f'<ds:DigestMethod Algorithm="{DIGEST_ALGO}"/>'
        f'<ds:DigestValue>{doc_digest}</ds:DigestValue>'
        f'</ds:Reference>'
        f'<ds:Reference Id="Reference-SignedProperties" Type="http://uri.etsi.org/01903#SignedProperties" URI="#{signed_props_id}">'
        f'<ds:Transforms>'
        f'<ds:Transform Algorithm="{C14N_ALGO}"/>'
        f'</ds:Transforms>'
        f'<ds:DigestMethod Algorithm="{DIGEST_ALGO}"/>'
        f'<ds:DigestValue>{signed_props_digest}</ds:DigestValue>'
        f'</ds:Reference>'
        f'</ds:SignedInfo>'
    )
    signed_info_c14n = _c14n(signed_info_xml)

    # 5. Firmar criptográficamente el SignedInfo con la llave privada RSA
    sig_bytes = key.sign(
        signed_info_c14n,
        padding.PKCS1v15(),
        hashes.SHA256()
    )
    sig_value_b64 = _b64(sig_bytes)

    # 6. Ensamblar el bloque completo <ds:Signature>
    signature_xml = (
        f'<ds:Signature xmlns:ds="{DS_NS}" Id="{sig_id}">'
        f'{signed_info_xml}'
        f'<ds:SignatureValue Id="{sig_val_id}">{sig_value_b64}</ds:SignatureValue>'
        f'<ds:KeyInfo Id="{key_info_id}">'
        f'<ds:X509Data>'
        f'<ds:X509Certificate>{cert_b64}</ds:X509Certificate>'
        f'</ds:X509Data>'
        f'</ds:KeyInfo>'
        f'<ds:Object Id="Object-{uid}">'
        f'<xades:QualifyingProperties xmlns:xades="{XADES_NS}" Target="#{sig_id}">'
        f'{signed_props_xml}'
        f'</xades:QualifyingProperties>'
        f'</ds:Object>'
        f'</ds:Signature>'
    )

    last_close = clean_xml.rfind('</')
    if last_close == -1:
        raise SignerError("El XML no contiene una etiqueta de cierre válida")

    signed_xml = clean_xml[:last_close].rstrip() + '\n' + signature_xml + '\n' + clean_xml[last_close:]
    return signed_xml


def verify_xml_signature(signed_xml_str: str) -> bool:
    """Verifica que el XML firmado contenga una estructura XAdES-BES válida y consistente."""
    try:
        if isinstance(signed_xml_str, bytes):
            signed_xml_str = signed_xml_str.decode('utf-8')
        root = ET.fromstring(signed_xml_str.encode('utf-8'))
        sig = root.find(f"{{{DS_NS}}}Signature")
        if sig is None:
            return False

        # Verificar presencia de SignedInfo, SignatureValue y KeyInfo
        si = sig.find(f"{{{DS_NS}}}SignedInfo")
        sv = sig.find(f"{{{DS_NS}}}SignatureValue")
        ki = sig.find(f"{{{DS_NS}}}KeyInfo")
        if si is None or sv is None or ki is None:
            return False

        cert_el = ki.find(f".//{{{DS_NS}}}X509Certificate")
        if cert_el is None or not cert_el.text:
            return False

        # Cargar certificado X.509
        from cryptography import x509
        cert_bytes = base64.b64decode(cert_el.text.strip())
        cert = x509.load_der_x509_certificate(cert_bytes)

        # Extraer SignedInfo canonicalizado para la verificación
        si_match = re.search(r'(<ds:SignedInfo.*?</ds:SignedInfo>)', signed_xml_str, re.DOTALL)
        if not si_match:
            return False
        signed_info_c14n = _c14n(si_match.group(1))

        # Verificar la firma RSA del SignedInfo canonicalizado
        sig_val = base64.b64decode(sv.text.strip())

        cert.public_key().verify(
            sig_val,
            signed_info_c14n,
            padding.PKCS1v15(),
            hashes.SHA256()
        )
        return True
    except Exception:
        return False
