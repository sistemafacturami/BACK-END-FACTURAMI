"""
FACTURAMI - Firmado Digital de Comprobantes Electrónicos (XAdES-BES / XAdES-EPES).
==================================================================================
Estándar oficial del Ministerio de Hacienda de Costa Rica (DGT).
Utiliza el certificado criptográfico en formato PKCS#12 (.p12) y su PIN,
descifrados en memoria desde `secret_store` (nunca se guardan en texto plano en disco).
Produce una firma digital XML Enveloped estándar XAdES (ETSI TS 101 903)
con Exclusive Canonicalization (W3C xml-exc-c14n#) y política de firma oficial de Costa Rica.
"""
import base64
import hashlib
import uuid
from datetime import datetime, timezone

from lxml import etree
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography import x509

C14N_EXC = "http://www.w3.org/2001/10/xml-exc-c14n#"
SIG_ALGO = "http://www.w3.org/2001/04/xmldsig-more#rsa-sha256"
DIGEST_ALGO = "http://www.w3.org/2001/04/xmlenc#sha256"
ENVELOPED_SIG = "http://www.w3.org/2000/09/xmldsig#enveloped-signature"
XADES_NS = "http://uri.etsi.org/01903/v1.3.2#"
DS_NS = "http://www.w3.org/2000/09/xmldsig#"

POLICY_ID = "https://tribunet.hacienda.go.cr/docs/esquemas/2016/v4.4/Resolucion_General_MH-DGT-RES-0027-2024.pdf"
POLICY_DIGEST = "KN6/2eZEAduZ3t0sn4aWMv8nmpGdaIcSso99Bp9QT04="


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


def sign_xml_xades_bes(xml_content: str, p12_bytes: bytes, pin: str) -> str:
    """
    Firma un comprobante electrónico XML con estándar XAdES-BES/EPES de Costa Rica.
    Inserta el bloque <ds:Signature> al final del elemento raíz preservando
    la integridad criptográfica exacta y los esquemas oficiales de Hacienda.
    """
    key, cert = load_p12_credentials(p12_bytes, pin)

    if isinstance(xml_content, str):
        xml_bytes = xml_content.encode('utf-8')
    else:
        xml_bytes = xml_content

    parser = etree.XMLParser(remove_blank_text=True)
    try:
        doc = etree.fromstring(xml_bytes, parser)
    except Exception as e:
        raise SignerError(f"Error parseando XML para firma: {e}")

    # Remover cualquier firma previa si existiera
    for old_sig in doc.findall(f".//{{{DS_NS}}}Signature"):
        doc.remove(old_sig)

    # Identificadores únicos para los nodos de firma
    uid = uuid.uuid4().hex[:12]
    sig_id = f"Signature-{uid}"
    sig_val_id = f"SignatureValue-{uid}"
    key_info_id = f"KeyInfo-{uid}"
    signed_props_id = f"SignedProperties-{uid}"

    # 1. Digest del documento raíz (Enveloped: canonizado antes de insertar ds:Signature)
    doc_c14n = etree.tostring(doc, method="c14n", exclusive=True, with_comments=False)
    doc_digest = base64.b64encode(hashlib.sha256(doc_c14n).digest()).decode('ascii')

    # 2. Metadatos del certificado del emisor
    der_cert = cert.public_bytes(serialization.Encoding.DER)
    cert_digest = base64.b64encode(hashlib.sha256(der_cert).digest()).decode('ascii')
    cert_b64 = base64.b64encode(der_cert).decode('ascii')
    signing_time = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    issuer_str = cert.issuer.rfc4514_string()
    serial_str = str(cert.serial_number)

    # 3. Construir la estructura completa de firma en el DOM
    sig_elem = etree.SubElement(doc, f"{{{DS_NS}}}Signature", nsmap={'ds': DS_NS}, Id=sig_id)

    # Construir Object y QualifyingProperties con SignedProperties
    obj_elem = etree.SubElement(sig_elem, f"{{{DS_NS}}}Object", nsmap={'ds': DS_NS}, Id=f"Object-{uid}")
    qp_elem = etree.SubElement(obj_elem, f"{{{XADES_NS}}}QualifyingProperties", nsmap={'xades': XADES_NS}, Target=f"#{sig_id}")
    sp_elem = etree.SubElement(qp_elem, f"{{{XADES_NS}}}SignedProperties", nsmap={'xades': XADES_NS, 'ds': DS_NS}, Id=signed_props_id)
    ssp_elem = etree.SubElement(sp_elem, f"{{{XADES_NS}}}SignedSignatureProperties", nsmap={'xades': XADES_NS, 'ds': DS_NS})

    # SigningTime
    st_elem = etree.SubElement(ssp_elem, f"{{{XADES_NS}}}SigningTime", nsmap={'xades': XADES_NS})
    st_elem.text = signing_time

    # SigningCertificate
    sc_elem = etree.SubElement(ssp_elem, f"{{{XADES_NS}}}SigningCertificate", nsmap={'xades': XADES_NS})
    c_elem = etree.SubElement(sc_elem, f"{{{XADES_NS}}}Cert", nsmap={'xades': XADES_NS})
    cd_elem = etree.SubElement(c_elem, f"{{{XADES_NS}}}CertDigest", nsmap={'xades': XADES_NS})
    etree.SubElement(cd_elem, f"{{{DS_NS}}}DigestMethod", nsmap={'ds': DS_NS}, Algorithm=DIGEST_ALGO)
    dv_elem = etree.SubElement(cd_elem, f"{{{DS_NS}}}DigestValue", nsmap={'ds': DS_NS})
    dv_elem.text = cert_digest

    iss_elem = etree.SubElement(c_elem, f"{{{XADES_NS}}}IssuerSerial", nsmap={'xades': XADES_NS})
    in_elem = etree.SubElement(iss_elem, f"{{{DS_NS}}}X509IssuerName", nsmap={'ds': DS_NS})
    in_elem.text = issuer_str
    sn_elem = etree.SubElement(iss_elem, f"{{{DS_NS}}}X509SerialNumber", nsmap={'ds': DS_NS})
    sn_elem.text = serial_str

    # SignaturePolicyIdentifier (Requerido por Ministerio de Hacienda de Costa Rica para XAdES-EPES)
    spi_elem = etree.SubElement(ssp_elem, f"{{{XADES_NS}}}SignaturePolicyIdentifier", nsmap={'xades': XADES_NS})
    spid_elem = etree.SubElement(spi_elem, f"{{{XADES_NS}}}SignaturePolicyId", nsmap={'xades': XADES_NS})
    sp_id_elem = etree.SubElement(spid_elem, f"{{{XADES_NS}}}SigPolicyId", nsmap={'xades': XADES_NS})
    id_elem = etree.SubElement(sp_id_elem, f"{{{XADES_NS}}}Identifier", nsmap={'xades': XADES_NS})
    id_elem.text = POLICY_ID
    etree.SubElement(sp_id_elem, f"{{{XADES_NS}}}Description", nsmap={'xades': XADES_NS})

    sph_elem = etree.SubElement(spid_elem, f"{{{XADES_NS}}}SigPolicyHash", nsmap={'xades': XADES_NS})
    etree.SubElement(sph_elem, f"{{{DS_NS}}}DigestMethod", nsmap={'ds': DS_NS}, Algorithm=DIGEST_ALGO)
    pdv_elem = etree.SubElement(sph_elem, f"{{{DS_NS}}}DigestValue", nsmap={'ds': DS_NS})
    pdv_elem.text = POLICY_DIGEST

    # 4. Calcular el Digest exacto de SignedProperties mientras está insertado en el DOM
    sp_c14n = etree.tostring(sp_elem, method="c14n", exclusive=True, with_comments=False)
    sp_digest = base64.b64encode(hashlib.sha256(sp_c14n).digest()).decode('ascii')

    # 5. Construir ds:SignedInfo con ambas referencias
    si_elem = etree.Element(f"{{{DS_NS}}}SignedInfo", nsmap={'ds': DS_NS})
    etree.SubElement(si_elem, f"{{{DS_NS}}}CanonicalizationMethod", Algorithm=C14N_EXC)
    etree.SubElement(si_elem, f"{{{DS_NS}}}SignatureMethod", Algorithm=SIG_ALGO)

    # Referencia al documento principal (Enveloped Signature)
    ref_doc = etree.SubElement(si_elem, f"{{{DS_NS}}}Reference", Id="Reference-Document", URI="")
    transforms = etree.SubElement(ref_doc, f"{{{DS_NS}}}Transforms")
    etree.SubElement(transforms, f"{{{DS_NS}}}Transform", Algorithm=ENVELOPED_SIG)
    etree.SubElement(transforms, f"{{{DS_NS}}}Transform", Algorithm=C14N_EXC)
    etree.SubElement(ref_doc, f"{{{DS_NS}}}DigestMethod", Algorithm=DIGEST_ALGO)
    rdv = etree.SubElement(ref_doc, f"{{{DS_NS}}}DigestValue")
    rdv.text = doc_digest

    # Referencia a las propiedades firmadas XAdES
    ref_sp = etree.SubElement(si_elem, f"{{{DS_NS}}}Reference", Id="Reference-SignedProperties", Type="http://uri.etsi.org/01903#SignedProperties", URI=f"#{signed_props_id}")
    sp_transforms = etree.SubElement(ref_sp, f"{{{DS_NS}}}Transforms")
    etree.SubElement(sp_transforms, f"{{{DS_NS}}}Transform", Algorithm=C14N_EXC)
    etree.SubElement(ref_sp, f"{{{DS_NS}}}DigestMethod", Algorithm=DIGEST_ALGO)
    rspdv = etree.SubElement(ref_sp, f"{{{DS_NS}}}DigestValue")
    rspdv.text = sp_digest

    # 6. Canonicalizar y firmar criptográficamente SignedInfo con la llave privada RSA
    si_c14n = etree.tostring(si_elem, method="c14n", exclusive=True, with_comments=False)
    sig_bytes = key.sign(si_c14n, padding.PKCS1v15(), hashes.SHA256())
    sig_value_b64 = base64.b64encode(sig_bytes).decode('ascii')

    # 7. Ensamblar los elementos hijos en ds:Signature en el orden estricto de XMLDSig:
    # 1: SignedInfo, 2: SignatureValue, 3: KeyInfo, 4: Object
    sig_elem.insert(0, si_elem)

    sv_elem = etree.Element(f"{{{DS_NS}}}SignatureValue", Id=sig_val_id)
    sv_elem.text = sig_value_b64
    sig_elem.insert(1, sv_elem)

    ki_elem = etree.Element(f"{{{DS_NS}}}KeyInfo", Id=key_info_id)
    x509_data = etree.SubElement(ki_elem, f"{{{DS_NS}}}X509Data")
    x509_cert = etree.SubElement(x509_data, f"{{{DS_NS}}}X509Certificate")
    x509_cert.text = cert_b64
    sig_elem.insert(2, ki_elem)

    return etree.tostring(doc, encoding='utf-8', xml_declaration=True).decode('utf-8')


def verify_xml_signature(signed_xml_str: str) -> bool:
    """Verifica que el XML firmado contenga una estructura XAdES válida y criptográficamente consistente."""
    try:
        if isinstance(signed_xml_str, str):
            xml_bytes = signed_xml_str.encode('utf-8')
        else:
            xml_bytes = signed_xml_str

        parser = etree.XMLParser(remove_blank_text=True)
        doc = etree.fromstring(xml_bytes, parser)

        DS = f"{{{DS_NS}}}"
        XADES = f"{{{XADES_NS}}}"

        sig = doc.find(f".//{DS}Signature")
        if sig is None:
            return False

        si = sig.find(f"{DS}SignedInfo")
        sv = sig.find(f"{DS}SignatureValue")
        ki = sig.find(f"{DS}KeyInfo")
        if si is None or sv is None or ki is None:
            return False

        cert_el = ki.find(f".//{DS}X509Certificate")
        if cert_el is None or not cert_el.text:
            return False

        # Cargar certificado X.509
        cert_bytes = base64.b64decode(cert_el.text.strip())
        cert = x509.load_der_x509_certificate(cert_bytes)

        # 1. Verificar firma RSA sobre SignedInfo
        si_c14n = etree.tostring(si, method="c14n", exclusive=True, with_comments=False)
        sig_val = base64.b64decode(sv.text.strip())
        cert.public_key().verify(
            sig_val,
            si_c14n,
            padding.PKCS1v15(),
            hashes.SHA256()
        )

        # 2. Verificar Document Reference Digest
        ref_doc = si.find(f"{DS}Reference[@URI='']")
        if ref_doc is not None:
            expected_doc_digest = ref_doc.find(f"{DS}DigestValue").text.strip()
            doc.remove(sig)
            doc_c14n = etree.tostring(doc, method="c14n", exclusive=True, with_comments=False)
            actual_doc_digest = base64.b64encode(hashlib.sha256(doc_c14n).digest()).decode('ascii')
            if expected_doc_digest != actual_doc_digest:
                return False

        # 3. Verificar SignedProperties Reference Digest
        doc_reloaded = etree.fromstring(xml_bytes, parser)
        ref_sp = doc_reloaded.find(f".//{DS}Reference[@Type='http://uri.etsi.org/01903#SignedProperties']")
        sp_elem = doc_reloaded.find(f".//{XADES}SignedProperties")
        if ref_sp is not None and sp_elem is not None:
            expected_sp_digest = ref_sp.find(f"{DS}DigestValue").text.strip()
            sp_c14n = etree.tostring(sp_elem, method="c14n", exclusive=True, with_comments=False)
            actual_sp_digest = base64.b64encode(hashlib.sha256(sp_c14n).digest()).decode('ascii')
            if expected_sp_digest != actual_sp_digest:
                return False

        return True
    except Exception:
        return False
