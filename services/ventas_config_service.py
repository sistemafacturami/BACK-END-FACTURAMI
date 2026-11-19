"""
FACTURAMI - Configuración de Facturación Electrónica por empresa (Fase 3).
Edición validada de datos generales, terminal/consecutivos, credenciales y certificado de Hacienda
(cifrados), y usuarios internos con rol. Cada cambio queda en audit_log dentro de la misma transacción.
NUNCA se registra ni se devuelve el valor de un secreto.
"""
import base64
import re
import uuid
from datetime import datetime, timezone

import db as database
import services.secret_store as secret_store
from services.ventas_authz import ROLES

MAX_CERT_BYTES = 200 * 1024
DIGITS_RE = re.compile(r'^\d+$')
EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')
USERNAME_RE = re.compile(r'^[a-z0-9._-]{3,32}$')

GENERAL_TEXT_FIELDS = {
    'nombre_comercial': 120, 'razon_social': 120, 'actividad_principal': 20,
    'provincia': 2, 'canton': 2, 'distrito': 2, 'otras_senas': 250,
    'telefono': 30, 'sitio_web': 120,
}


class ConfigError(ValueError):
    pass


def _clean_text(value, max_len, field):
    v = str(value if value is not None else '').strip()
    if len(v) > max_len:
        raise ConfigError(f"'{field}' excede {max_len} caracteres")
    return v


def update_general_settings(company_id, body, actor):
    updates = {}
    for field, max_len in GENERAL_TEXT_FIELDS.items():
        if field in body:
            updates[field] = _clean_text(body[field], max_len, field)
    for field in ('provincia', 'canton', 'distrito'):
        if field in updates and updates[field] and not DIGITS_RE.match(updates[field]):
            raise ConfigError(f"'{field}' debe ser numerico")
    for field in ('email', 'correo_envio_comprobantes'):
        if field in body:
            v = _clean_text(body[field], 120, field)
            if v and not EMAIL_RE.match(v):
                raise ConfigError(f"'{field}' no es un correo valido")
            updates[field] = v
    if 'moneda_default' in body:
        if body['moneda_default'] not in ('CRC', 'USD'):
            raise ConfigError("'moneda_default' debe ser CRC o USD")
        updates['moneda_default'] = body['moneda_default']
    if 'tipo_identificacion' in body:
        if body['tipo_identificacion'] not in ('01', '02', '03', '04'):
            raise ConfigError("'tipo_identificacion' invalido")
        updates['tipo_identificacion'] = body['tipo_identificacion']
    if 'identificacion' in body:
        v = _clean_text(body['identificacion'], 20, 'identificacion').replace('-', '').replace(' ', '')
        if not DIGITS_RE.match(v):
            raise ConfigError("'identificacion' debe contener solo digitos")
        updates['identificacion'] = v
    for field in ('condicion_venta_default', 'medio_pago_default'):
        if field in body:
            v = str(body[field]).strip()
            if not re.match(r'^\d{2}$', v):
                raise ConfigError(f"'{field}' debe ser un codigo de 2 digitos")
            updates[field] = v
    if 'plazo_credito_default' in body:
        try:
            plazo = int(body['plazo_credito_default'])
        except (TypeError, ValueError):
            raise ConfigError("'plazo_credito_default' debe ser entero")
        if not 0 <= plazo <= 365:
            raise ConfigError("'plazo_credito_default' debe estar entre 0 y 365")
        updates['plazo_credito_default'] = plazo
    if 'actividades_secundarias' in body:
        acts = body['actividades_secundarias']
        if not isinstance(acts, list) or len(acts) > 20 or any(not isinstance(a, str) or len(a) > 20 for a in acts):
            raise ConfigError("'actividades_secundarias' debe ser una lista de hasta 20 codigos")
        import json
        updates['actividades_secundarias_json'] = json.dumps([a.strip() for a in acts], ensure_ascii=False)

    if not updates:
        raise ConfigError('No se enviaron campos validos para actualizar')

    with database.get_db() as conn:
        sets = ', '.join(f"{k} = ?" for k in updates)
        conn.execute(
            f"UPDATE company_fe_settings SET {sets}, updated_at = datetime('now','localtime') WHERE company_id = ?",
            (*updates.values(), company_id)
        )
        database.insert_audit_log(conn, 'ventas_config_general', actor, 'company', company_id,
                                  {'campos': sorted(updates.keys())})
    return sorted(updates.keys())


def update_terminal(company_id, body, actor):
    """Edita codigos de sucursal/terminal y consecutivos. Los consecutivos solo se pueden fijar
    mientras la empresa no haya emitido ningun comprobante (evita saltos/duplicados ante Hacienda)."""
    suc = str(body.get('codigo_sucursal', '')).strip()
    ter = str(body.get('codigo_terminal', '')).strip()
    if not re.match(r'^\d{3}$', suc):
        raise ConfigError("'codigo_sucursal' debe tener 3 digitos")
    if not re.match(r'^\d{5}$', ter):
        raise ConfigError("'codigo_terminal' debe tener 5 digitos")
    consec_fields = ('consec_fe', 'consec_te', 'consec_nc', 'consec_nd')
    consecs = {}
    for f in consec_fields:
        if f in body:
            try:
                n = int(body[f])
            except (TypeError, ValueError):
                raise ConfigError(f"'{f}' debe ser entero")
            if not 0 <= n <= 9999999999:
                raise ConfigError(f"'{f}' fuera de rango")
            consecs[f] = n

    with database.get_db() as conn:
        term = conn.execute(
            "SELECT * FROM company_terminals WHERE company_id = ? ORDER BY codigo_sucursal, codigo_terminal LIMIT 1",
            (company_id,)
        ).fetchone()
        if not term:
            raise ConfigError('La empresa no tiene terminal inicializada')
        emitted = conn.execute(
            "SELECT COUNT(*) FROM electronic_documents WHERE company_id = ? AND estado_comprobante != 'borrador'",
            (company_id,)
        ).fetchone()[0]
        if emitted and (consecs or suc != term['codigo_sucursal'] or ter != term['codigo_terminal']):
            raise ConfigError('No se pueden cambiar codigos ni consecutivos: ya hay comprobantes emitidos')
        conn.execute("""
            UPDATE company_terminals SET codigo_sucursal = ?, codigo_terminal = ?
            WHERE id = ? AND company_id = ?
        """, (suc, ter, term['id'], company_id))
        conn.execute("UPDATE company_branches SET codigo_sucursal = ? WHERE id = ? AND company_id = ?",
                     (suc, term['branch_id'], company_id))
        for f, n in consecs.items():
            conn.execute(f"UPDATE company_terminals SET {f} = ? WHERE id = ? AND company_id = ?",
                         (n, term['id'], company_id))
        database.insert_audit_log(conn, 'ventas_config_terminal', actor, 'company', company_id,
                                  {'codigo_sucursal': suc, 'codigo_terminal': ter,
                                   'consecutivos_modificados': sorted(consecs.keys())})


def _cert_metadata(p12_bytes, pin):
    try:
        from cryptography.hazmat.primitives.serialization import pkcs12
        key, cert, _extra = pkcs12.load_key_and_certificates(p12_bytes, pin.encode('utf-8'))
    except Exception:
        raise ConfigError('No se pudo abrir el certificado .p12: archivo invalido o PIN incorrecto')
    if cert is None or key is None:
        raise ConfigError('El .p12 no contiene llave privada y certificado')
    expira = getattr(cert, 'not_valid_after_utc', None)
    if expira is None:
        expira = cert.not_valid_after.replace(tzinfo=timezone.utc)
    if expira <= datetime.now(timezone.utc):
        raise ConfigError('El certificado esta vencido')
    try:
        subject = cert.subject.rfc4514_string()[:200]
    except Exception:
        subject = ''
    return expira.date().isoformat(), subject


def update_hacienda_credentials(company_id, body, actor):
    """Guarda credenciales ATV y certificado cifrados. Devuelve solo metadatos de estado."""
    if not secret_store.is_configured():
        raise secret_store.SecretStoreError(
            'Almacen de secretos no configurado: defina FACTURAMI_FE_SECRET_KEY e instale cryptography')

    changed = []
    with database.get_db() as conn:
        st = conn.execute("SELECT * FROM company_fe_settings WHERE company_id = ?", (company_id,)).fetchone()
        if not st:
            raise ConfigError('Configuracion de empresa no inicializada')
        sets, vals = {}, []

        if body.get('username') is not None:
            u = _clean_text(body['username'], 120, 'username')
            sets['hacienda_username'] = u
            changed.append('usuario')
        if body.get('password'):
            sets['hacienda_password_enc'] = secret_store.encrypt(company_id, str(body['password']))
            changed.append('password')

        pin_plain = None
        if body.get('pin'):
            pin_plain = str(body['pin'])
            if len(pin_plain) > 64:
                raise ConfigError("'pin' demasiado largo")
            sets['cert_pin_enc'] = secret_store.encrypt(company_id, pin_plain)
            changed.append('pin')

        if body.get('cert_p12_b64'):
            try:
                raw = base64.b64decode(str(body['cert_p12_b64']), validate=True)
            except Exception:
                raise ConfigError('Certificado: base64 invalido')
            if not raw or len(raw) > MAX_CERT_BYTES:
                raise ConfigError(f'Certificado vacio o mayor a {MAX_CERT_BYTES // 1024} KB')
            if pin_plain is None:
                if not st['cert_pin_enc']:
                    raise ConfigError('Envie el PIN junto con el certificado')
                pin_plain = secret_store.decrypt(company_id, st['cert_pin_enc']).decode('utf-8')
            expira, subject = _cert_metadata(raw, pin_plain)
            sets['cert_file_enc'] = secret_store.encrypt(company_id, raw)
            sets['has_cert_file'] = 1
            sets['cert_expira'] = expira
            sets['cert_subject'] = subject
            changed.append('certificado')

        if body.get('ambiente') is not None:
            amb = body['ambiente']
            if amb not in ('stag', 'prod'):
                raise ConfigError("'ambiente' debe ser stag o prod")
            if amb == 'prod':
                has_user = bool(sets.get('hacienda_username', st['hacienda_username']))
                has_pw = bool(sets.get('hacienda_password_enc', st['hacienda_password_enc']))
                has_cert = bool(sets.get('has_cert_file', st['has_cert_file']))
                if not (has_user and has_pw and has_cert):
                    raise ConfigError('Para produccion se requieren usuario, password y certificado cargados')
            sets['ambiente_hacienda'] = amb
            changed.append('ambiente')

        if not sets:
            raise ConfigError('No se enviaron campos para actualizar')
        cols = ', '.join(f"{k} = ?" for k in sets)
        conn.execute(
            f"UPDATE company_fe_settings SET {cols}, updated_at = datetime('now','localtime') WHERE company_id = ?",
            (*sets.values(), company_id)
        )
        # Solo se registra QUE cambió, jamás el valor
        database.insert_audit_log(conn, 'ventas_config_hacienda', actor, 'company', company_id,
                                  {'cambios': changed})
    return changed


def list_company_users(company_id):
    with database.get_db() as conn:
        owner = conn.execute("SELECT cuenta_id FROM clientes WHERE id = ?", (company_id,)).fetchone()
        rows = conn.execute(
            "SELECT username, role, activo, created_by, created_at FROM company_users WHERE company_id = ? ORDER BY username",
            (company_id,)
        ).fetchall()
    return {'owner': (owner['cuenta_id'] if owner else ''), 'users': [dict(r) for r in rows]}


def upsert_company_user(company_id, body, actor):
    username = str(body.get('username', '')).strip().lower()
    role = str(body.get('role', '')).strip()
    if not USERNAME_RE.match(username):
        raise ConfigError('Usuario invalido')
    if role not in ROLES:
        raise ConfigError(f"Rol invalido. Opciones: {', '.join(ROLES)}")
    with database.get_db() as conn:
        owner = conn.execute("SELECT cuenta_id FROM clientes WHERE id = ?", (company_id,)).fetchone()
        if owner and str(owner['cuenta_id']).strip().lower() == username:
            raise ConfigError('El dueño de la cuenta ya es administrador de la empresa')
        exists = conn.execute("SELECT 1 FROM cuentas WHERE LOWER(TRIM(username)) = ?", (username,)).fetchone()
        if not exists:
            raise LookupError('Usuario no encontrado')
        conn.execute("""
            INSERT INTO company_users (id, company_id, username, role, activo, created_by)
            VALUES (?, ?, ?, ?, 1, ?)
            ON CONFLICT(company_id, username) DO UPDATE SET role = excluded.role, activo = 1
        """, (str(uuid.uuid4()), company_id, username, role, actor))
        database.insert_audit_log(conn, 'ventas_usuario_asignado', actor, 'company', company_id,
                                  {'usuario': username, 'rol': role})


def deactivate_company_user(company_id, username, actor):
    username = str(username or '').strip().lower()
    with database.get_db() as conn:
        cur = conn.execute(
            "UPDATE company_users SET activo = 0 WHERE company_id = ? AND LOWER(username) = ?",
            (company_id, username)
        )
        if cur.rowcount == 0:
            raise LookupError('Usuario no encontrado en la empresa')
        database.insert_audit_log(conn, 'ventas_usuario_desactivado', actor, 'company', company_id,
                                  {'usuario': username})
