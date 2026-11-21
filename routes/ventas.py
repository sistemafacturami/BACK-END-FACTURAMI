"""
FACTURAMI - Rutas del Sistema de Ventas y Facturación Electrónica.
Blueprint: /api/ventas/*

Seguridad Multiempresa (Fases 2-3):
  - Todo endpoint exige JWT válido (@auth_required).
  - Cada operación resuelve el rol del usuario sobre la empresa (dueño = admin_empresa,
    o usuario interno en company_users) y exige un permiso concreto. Sin acceso: 403 genérico.
  - El admin global de FACTURAMI NO accede al contenido comercial de las empresas.
  - Los secretos (password, PIN, certificado) jamás se devuelven.
"""
import io
from flask import Blueprint, request, Response, send_file
from flask_jwt_extended import get_jwt_identity
from errors import success, error, handle_errors
from services.authz import auth_required
from services.ventas_authz import require_company_permission, permissions_for
import services.ventas_service as ventas_service
import services.ventas_config_service as cfg_service
import services.ventas_comercial_service as comercial_service
import services.fe_emission_service as emission_service
import services.ventas_gestion_service as gestion_service
import services.ventas_contabilidad_service as contabilidad_ventas_service
import services.ventas_reportes_service as reportes_service
import services.fe_mail_service as mail_service
import services.secret_store as secret_store
import db as database

ventas_bp = Blueprint('ventas', __name__)


def _company_id_from(source):
    return str((source or {}).get('company_id') or '').strip()


def _body():
    data = request.get_json(force=True, silent=True)
    return data if isinstance(data, dict) else {}


def _context_with_role(company_id, role):
    ctx = ventas_service.get_or_init_company_context(company_id, current_user=get_jwt_identity() or '')
    ctx['role'] = role
    ctx['permissions'] = permissions_for(role)
    ctx['secret_store_ready'] = secret_store.is_configured()
    return ctx


@ventas_bp.route('/api/ventas/context', methods=['GET'])
@auth_required
@handle_errors
def ventas_context():
    """Contexto automático de la empresa activa (perfil, sucursal, terminal, rol y permisos)."""
    company_id = _company_id_from(request.args)
    if not company_id:
        return error("Se requiere el parámetro company_id", 400)
    role, denied = require_company_permission(company_id, 'dashboard.view')
    if denied:
        return denied
    return success({'data': _context_with_role(company_id, role)})


@ventas_bp.route('/api/ventas/dashboard', methods=['GET'])
@auth_required
@handle_errors
def ventas_dashboard():
    """10 indicadores y 7 series estadísticas filtradas por empresa."""
    company_id = _company_id_from(request.args)
    if not company_id:
        return error("Se requiere el parámetro company_id", 400)
    _role, denied = require_company_permission(company_id, 'dashboard.view')
    if denied:
        return denied
    data = ventas_service.get_dashboard_stats(
        company_id=company_id,
        fecha_inicio=(request.args.get('fecha_inicio') or '').strip(),
        fecha_fin=(request.args.get('fecha_fin') or '').strip(),
        mes=(request.args.get('mes') or '').strip(),
        anio=(request.args.get('anio') or '').strip(),
        customer_id=(request.args.get('customer_id') or '').strip(),
        estado=(request.args.get('estado') or '').strip(),
    )
    return success({'data': data})


# ── Fase 3: configuración de facturación electrónica ───────────────────────

@ventas_bp.route('/api/ventas/config', methods=['GET'])
@auth_required
@handle_errors
def config_get():
    company_id = _company_id_from(request.args)
    if not company_id:
        return error("Se requiere el parámetro company_id", 400)
    role, denied = require_company_permission(company_id, 'config.view')
    if denied:
        return denied
    return success({'data': _context_with_role(company_id, role)})


@ventas_bp.route('/api/ventas/config', methods=['PUT'])
@auth_required
@handle_errors
def config_put():
    body = _body()
    company_id = _company_id_from(body)
    if not company_id:
        return error("Se requiere company_id", 400)
    _role, denied = require_company_permission(company_id, 'config.write')
    if denied:
        return denied
    ventas_service.get_or_init_company_context(company_id)
    try:
        campos = cfg_service.update_general_settings(company_id, body, get_jwt_identity())
    except cfg_service.ConfigError as e:
        return error(str(e), 400)
    return success({'campos_actualizados': campos}, message='Configuración actualizada')


@ventas_bp.route('/api/ventas/config/terminal', methods=['PUT'])
@auth_required
@handle_errors
def config_terminal_put():
    body = _body()
    company_id = _company_id_from(body)
    if not company_id:
        return error("Se requiere company_id", 400)
    _role, denied = require_company_permission(company_id, 'config.write')
    if denied:
        return denied
    ventas_service.get_or_init_company_context(company_id)
    try:
        cfg_service.update_terminal(company_id, body, get_jwt_identity())
    except cfg_service.ConfigError as e:
        return error(str(e), 409 if 'ya hay comprobantes' in str(e) else 400)
    return success(message='Sucursal, terminal y consecutivos actualizados')


@ventas_bp.route('/api/ventas/config/hacienda', methods=['PUT'])
@auth_required
@handle_errors
def config_hacienda_put():
    """Credenciales ATV, PIN y certificado .p12 (cifrados por empresa). Devuelve solo metadatos."""
    body = _body()
    company_id = _company_id_from(body)
    if not company_id:
        return error("Se requiere company_id", 400)
    role, denied = require_company_permission(company_id, 'config.secrets')
    if denied:
        return denied
    ventas_service.get_or_init_company_context(company_id)
    try:
        cambios = cfg_service.update_hacienda_credentials(company_id, body, get_jwt_identity())
    except secret_store.SecretStoreError as e:
        return error(str(e), 503)
    except cfg_service.ConfigError as e:
        return error(str(e), 400)
    return success({'data': _context_with_role(company_id, role), 'cambios': cambios},
                   message='Credenciales de Hacienda guardadas (cifradas)')


@ventas_bp.route('/api/ventas/config/smtp', methods=['PUT'])
@auth_required
@handle_errors
def config_smtp_put():
    body = _body()
    company_id = _company_id_from(body)
    if not company_id:
        return error("Se requiere company_id", 400)
    role, denied = require_company_permission(company_id, 'config.secrets')
    if denied:
        return denied
    ventas_service.get_or_init_company_context(company_id)
    try:
        cambios = cfg_service.update_smtp_settings(company_id, body, get_jwt_identity())
    except secret_store.SecretStoreError as e:
        return error(str(e), 503)
    except cfg_service.ConfigError as e:
        return error(str(e), 400)
    return success({'data': _context_with_role(company_id, role), 'cambios': cambios},
                   message='Configuración SMTP guardada')


@ventas_bp.route('/api/ventas/config/test-smtp', methods=['POST'])
@auth_required
@handle_errors
def config_test_smtp():
    body = _body()
    company_id = _company_id_from(body)
    if not company_id:
        return error("Se requiere company_id", 400)
    _role, denied = require_company_permission(company_id, 'config.secrets')
    if denied:
        return denied
    ventas_service.get_or_init_company_context(company_id)
    cfg = mail_service.get_company_smtp_config(company_id)
    if body.get('smtp_host'):
        cfg['host'] = body['smtp_host']
    if body.get('smtp_port'):
        try:
            cfg['port'] = int(body['smtp_port'])
        except (ValueError, TypeError):
            pass
    if body.get('smtp_user'):
        cfg['user'] = body['smtp_user']
    if body.get('smtp_password'):
        cfg['password'] = body['smtp_password']
    if 'smtp_tls' in body:
        cfg['tls'] = bool(body['smtp_tls'])
    try:
        res = mail_service.test_smtp_connection(cfg)
    except mail_service.MailError as e:
        return error(str(e), 400)
    return success({'data': res}, message='Conexión SMTP exitosa')


@ventas_bp.route('/api/ventas/users', methods=['GET'])
@auth_required
@handle_errors
def users_list():
    company_id = _company_id_from(request.args)
    if not company_id:
        return error("Se requiere el parámetro company_id", 400)
    _role, denied = require_company_permission(company_id, 'users.manage')
    if denied:
        return denied
    return success({'data': cfg_service.list_company_users(company_id)})


@ventas_bp.route('/api/ventas/users', methods=['POST'])
@auth_required
@handle_errors
def users_upsert():
    body = _body()
    company_id = _company_id_from(body)
    if not company_id:
        return error("Se requiere company_id", 400)
    _role, denied = require_company_permission(company_id, 'users.manage')
    if denied:
        return denied
    try:
        cfg_service.upsert_company_user(company_id, body, get_jwt_identity())
    except cfg_service.ConfigError as e:
        return error(str(e), 400)
    except LookupError as e:
        return error(str(e), 404)
    return success(message='Usuario asignado a la empresa')


@ventas_bp.route('/api/ventas/users/<username>', methods=['DELETE'])
@auth_required
@handle_errors
def users_delete(username):
    company_id = _company_id_from(request.args)
    if not company_id:
        return error("Se requiere el parámetro company_id", 400)
    _role, denied = require_company_permission(company_id, 'users.manage')
    if denied:
        return denied
    try:
        cfg_service.deactivate_company_user(company_id, username, get_jwt_identity())
    except LookupError as e:
        return error(str(e), 404)
    return success(message='Usuario desactivado')


# ── Fase 4: Administración Comercial ───────────────────────────────────────

# ── Clientes Comerciales ──

@ventas_bp.route('/api/ventas/customers', methods=['GET'])
@auth_required
@handle_errors
def customers_list():
    company_id = _company_id_from(request.args)
    if not company_id:
        return error("Se requiere el parámetro company_id", 400)
    _role, denied = require_company_permission(company_id, 'customers.view')
    if denied:
        return denied
    ventas_service.get_or_init_company_context(company_id)
    res = comercial_service.list_customers(
        company_id=company_id,
        q=request.args.get('q'),
        estado=request.args.get('estado', 'activo'),
        limit=request.args.get('limit', 100),
        offset=request.args.get('offset', 0)
    )
    return success({'data': res})


@ventas_bp.route('/api/ventas/customers', methods=['POST'])
@auth_required
@handle_errors
def customers_create():
    body = _body()
    company_id = _company_id_from(body)
    if not company_id:
        return error("Se requiere company_id", 400)
    _role, denied = require_company_permission(company_id, 'customers.manage')
    if denied:
        return denied
    ventas_service.get_or_init_company_context(company_id)
    try:
        c = comercial_service.create_customer(company_id, body, get_jwt_identity() or '')
    except comercial_service.ComercialError as e:
        return error(str(e), 400)
    return success({'data': c}, message='Cliente comercial creado exitosamente')


@ventas_bp.route('/api/ventas/customers/<customer_id>', methods=['GET'])
@auth_required
@handle_errors
def customers_get(customer_id):
    company_id = _company_id_from(request.args)
    if not company_id:
        return error("Se requiere el parámetro company_id", 400)
    _role, denied = require_company_permission(company_id, 'customers.view')
    if denied:
        return denied
    ventas_service.get_or_init_company_context(company_id)
    try:
        c = comercial_service.get_customer(company_id, customer_id)
    except comercial_service.ComercialError as e:
        return error(str(e), 404)
    return success({'data': c})


@ventas_bp.route('/api/ventas/customers/<customer_id>', methods=['PUT'])
@auth_required
@handle_errors
def customers_update(customer_id):
    body = _body()
    company_id = _company_id_from(body) or _company_id_from(request.args)
    if not company_id:
        return error("Se requiere company_id", 400)
    _role, denied = require_company_permission(company_id, 'customers.manage')
    if denied:
        return denied
    ventas_service.get_or_init_company_context(company_id)
    try:
        c = comercial_service.update_customer(company_id, customer_id, body, get_jwt_identity() or '')
    except comercial_service.ComercialError as e:
        return error(str(e), 400)
    return success({'data': c}, message='Cliente actualizado exitosamente')


@ventas_bp.route('/api/ventas/customers/<customer_id>', methods=['DELETE'])
@auth_required
@handle_errors
def customers_delete(customer_id):
    company_id = _company_id_from(request.args) or _company_id_from(_body())
    if not company_id:
        return error("Se requiere el parámetro company_id", 400)
    _role, denied = require_company_permission(company_id, 'customers.manage')
    if denied:
        return denied
    ventas_service.get_or_init_company_context(company_id)
    try:
        res = comercial_service.delete_customer(company_id, customer_id, get_jwt_identity() or '')
    except comercial_service.ComercialError as e:
        return error(str(e), 404)
    return success({'data': res}, message=res.get('message', 'Cliente eliminado'))


# ── Categorías ──

@ventas_bp.route('/api/ventas/categories', methods=['GET'])
@auth_required
@handle_errors
def categories_list():
    company_id = _company_id_from(request.args)
    if not company_id:
        return error("Se requiere el parámetro company_id", 400)
    _role, denied = require_company_permission(company_id, 'products.view')
    if denied:
        return denied
    ventas_service.get_or_init_company_context(company_id)
    activo_only = request.args.get('activo') in ('1', 'true', True)
    return success({'data': comercial_service.list_categories(company_id, activo_only=activo_only)})


@ventas_bp.route('/api/ventas/categories', methods=['POST'])
@auth_required
@handle_errors
def categories_create():
    body = _body()
    company_id = _company_id_from(body)
    if not company_id:
        return error("Se requiere company_id", 400)
    _role, denied = require_company_permission(company_id, 'products.manage')
    if denied:
        return denied
    ventas_service.get_or_init_company_context(company_id)
    try:
        cat = comercial_service.create_category(company_id, body, get_jwt_identity() or '')
    except comercial_service.ComercialError as e:
        return error(str(e), 400)
    return success({'data': cat}, message='Categoría creada')


@ventas_bp.route('/api/ventas/categories/<category_id>', methods=['PUT'])
@auth_required
@handle_errors
def categories_update(category_id):
    body = _body()
    company_id = _company_id_from(body) or _company_id_from(request.args)
    if not company_id:
        return error("Se requiere company_id", 400)
    _role, denied = require_company_permission(company_id, 'products.manage')
    if denied:
        return denied
    ventas_service.get_or_init_company_context(company_id)
    try:
        cat = comercial_service.update_category(company_id, category_id, body, get_jwt_identity() or '')
    except comercial_service.ComercialError as e:
        return error(str(e), 400)
    return success({'data': cat}, message='Categoría actualizada')


@ventas_bp.route('/api/ventas/categories/<category_id>', methods=['DELETE'])
@auth_required
@handle_errors
def categories_delete(category_id):
    company_id = _company_id_from(request.args) or _company_id_from(_body())
    if not company_id:
        return error("Se requiere el parámetro company_id", 400)
    _role, denied = require_company_permission(company_id, 'products.manage')
    if denied:
        return denied
    ventas_service.get_or_init_company_context(company_id)
    try:
        res = comercial_service.delete_category(company_id, category_id, get_jwt_identity() or '')
    except comercial_service.ComercialError as e:
        return error(str(e), 400)
    return success({'data': res}, message='Categoría eliminada')


# ── Catálogo de Productos y Servicios ──

@ventas_bp.route('/api/ventas/products', methods=['GET'])
@auth_required
@handle_errors
def products_list():
    company_id = _company_id_from(request.args)
    if not company_id:
        return error("Se requiere el parámetro company_id", 400)
    _role, denied = require_company_permission(company_id, 'products.view')
    if denied:
        return denied
    ventas_service.get_or_init_company_context(company_id)
    stock_bajo = request.args.get('stock_bajo') in ('1', 'true', True)
    res = comercial_service.list_products(
        company_id=company_id,
        q=request.args.get('q'),
        category_id=request.args.get('category_id'),
        tipo=request.args.get('tipo'),
        stock_bajo=stock_bajo,
        estado=request.args.get('estado', 'activo'),
        limit=request.args.get('limit', 100),
        offset=request.args.get('offset', 0)
    )
    return success({'data': res})


@ventas_bp.route('/api/ventas/products', methods=['POST'])
@auth_required
@handle_errors
def products_create():
    body = _body()
    company_id = _company_id_from(body)
    if not company_id:
        return error("Se requiere company_id", 400)
    _role, denied = require_company_permission(company_id, 'products.manage')
    if denied:
        return denied
    ventas_service.get_or_init_company_context(company_id)
    try:
        prod = comercial_service.create_product(company_id, body, get_jwt_identity() or '')
    except comercial_service.ComercialError as e:
        return error(str(e), 400)
    return success({'data': prod}, message='Producto o servicio creado')


@ventas_bp.route('/api/ventas/products/<product_id>', methods=['GET'])
@auth_required
@handle_errors
def products_get(product_id):
    company_id = _company_id_from(request.args)
    if not company_id:
        return error("Se requiere el parámetro company_id", 400)
    _role, denied = require_company_permission(company_id, 'products.view')
    if denied:
        return denied
    ventas_service.get_or_init_company_context(company_id)
    try:
        p = comercial_service.get_product(company_id, product_id)
    except comercial_service.ComercialError as e:
        return error(str(e), 404)
    return success({'data': p})


@ventas_bp.route('/api/ventas/products/<product_id>', methods=['PUT'])
@auth_required
@handle_errors
def products_update(product_id):
    body = _body()
    company_id = _company_id_from(body) or _company_id_from(request.args)
    if not company_id:
        return error("Se requiere company_id", 400)
    _role, denied = require_company_permission(company_id, 'products.manage')
    if denied:
        return denied
    ventas_service.get_or_init_company_context(company_id)
    try:
        p = comercial_service.update_product(company_id, product_id, body, get_jwt_identity() or '')
    except comercial_service.ComercialError as e:
        return error(str(e), 400)
    return success({'data': p}, message='Producto o servicio actualizado')


@ventas_bp.route('/api/ventas/products/<product_id>', methods=['DELETE'])
@auth_required
@handle_errors
def products_delete(product_id):
    company_id = _company_id_from(request.args) or _company_id_from(_body())
    if not company_id:
        return error("Se requiere el parámetro company_id", 400)
    _role, denied = require_company_permission(company_id, 'products.manage')
    if denied:
        return denied
    ventas_service.get_or_init_company_context(company_id)
    try:
        res = comercial_service.delete_product(company_id, product_id, get_jwt_identity() or '')
    except comercial_service.ComercialError as e:
        return error(str(e), 404)
    return success({'data': res}, message=res.get('message', 'Producto eliminado'))


@ventas_bp.route('/api/ventas/products/<product_id>/stock', methods=['POST'])
@auth_required
@handle_errors
def products_adjust_stock(product_id):
    body = _body()
    company_id = _company_id_from(body) or _company_id_from(request.args)
    if not company_id:
        return error("Se requiere company_id", 400)
    _role, denied = require_company_permission(company_id, 'products.manage')
    if denied:
        return denied
    ventas_service.get_or_init_company_context(company_id)
    try:
        p = comercial_service.adjust_stock(company_id, product_id, body, get_jwt_identity() or '')
    except comercial_service.ComercialError as e:
        return error(str(e), 400)
    return success({'data': p}, message='Stock ajustado correctamente')


# ── Parámetros auxiliares ──

@ventas_bp.route('/api/ventas/tax-rates', methods=['GET'])
@auth_required
@handle_errors
def tax_rates_list():
    return success({'data': comercial_service.get_tax_rates()})


@ventas_bp.route('/api/ventas/units', methods=['GET'])
@auth_required
@handle_errors
def units_list():
    return success({'data': comercial_service.get_unidades_medida()})


# ── Fase 5: Facturación Electrónica (Hacienda Costa Rica) ──────────────────

@ventas_bp.route('/api/ventas/documents', methods=['POST'])
@auth_required
@handle_errors
def documents_emit():
    body = _body()
    company_id = _company_id_from(body)
    if not company_id:
        return error("Se requiere company_id", 400)
    _role, denied = require_company_permission(company_id, 'sales.create')
    if denied:
        return denied
    ventas_service.get_or_init_company_context(company_id)
    try:
        doc = emission_service.emitir_comprobante(company_id, body, get_jwt_identity() or '')
    except emission_service.EmissionError as e:
        return error(str(e), 400)
    return success({'data': doc}, message=f"Comprobante {doc.get('consecutivo', '')} generado exitosamente")


@ventas_bp.route('/api/ventas/documents', methods=['GET'])
@auth_required
@handle_errors
def documents_list():
    company_id = _company_id_from(request.args)
    if not company_id:
        return error("Se requiere el parámetro company_id", 400)
    _role, denied = require_company_permission(company_id, 'docs.view')
    if denied:
        return denied
    ventas_service.get_or_init_company_context(company_id)
    res = emission_service.list_documents(
        company_id=company_id,
        tipo=request.args.get('tipo'),
        estado=request.args.get('estado'),
        fecha_inicio=request.args.get('fecha_inicio'),
        fecha_fin=request.args.get('fecha_fin'),
        q=request.args.get('q'),
        limit=request.args.get('limit', 50),
        offset=request.args.get('offset', 0)
    )
    return success({'data': res})


@ventas_bp.route('/api/ventas/documents/<document_id>', methods=['GET'])
@auth_required
@handle_errors
def documents_get(document_id):
    company_id = _company_id_from(request.args)
    if not company_id:
        return error("Se requiere el parámetro company_id", 400)
    _role, denied = require_company_permission(company_id, 'docs.view')
    if denied:
        return denied
    ventas_service.get_or_init_company_context(company_id)
    try:
        doc = emission_service.get_document(company_id, document_id)
    except emission_service.EmissionError as e:
        return error(str(e), 404)
    return success({'data': doc})


@ventas_bp.route('/api/ventas/documents/<document_id>/sync-hacienda', methods=['POST'])
@auth_required
@handle_errors
def documents_sync_hacienda(document_id):
    body = _body()
    company_id = _company_id_from(body) or _company_id_from(request.args)
    if not company_id:
        return error("Se requiere company_id", 400)
    _role, denied = require_company_permission(company_id, 'docs.view')
    if denied:
        return denied
    ventas_service.get_or_init_company_context(company_id)
    try:
        updated = emission_service.sync_hacienda_status(company_id, document_id, get_jwt_identity() or '')
    except emission_service.EmissionError as e:
        return error(str(e), 400)
    return success({'data': updated}, message=f"Estado de comprobante actualizado: {updated.get('estado_comprobante')}")


@ventas_bp.route('/api/ventas/documents/retry-queue', methods=['POST'])
@auth_required
@handle_errors
def documents_retry_queue():
    body = _body()
    company_id = _company_id_from(body) or _company_id_from(request.args)
    if not company_id:
        return error("Se requiere company_id", 400)
    _role, denied = require_company_permission(company_id, 'sales.create')
    if denied:
        return denied
    ventas_service.get_or_init_company_context(company_id)
    try:
        res = emission_service.retry_pending_hacienda_queue(
            company_id=company_id,
            actor=get_jwt_identity() or '',
            document_id=body.get('document_id'),
            limit=body.get('limit', 50)
        )
    except emission_service.EmissionError as e:
        return error(str(e), 400)
    return success({'data': res}, message=f"Cola procesada: {res.get('enviados', 0)} enviados, {res.get('fallidos', 0)} pendientes")


@ventas_bp.route('/api/ventas/documents/<document_id>/send-email', methods=['POST'])
@auth_required
@handle_errors
def documents_send_email(document_id):
    body = _body()
    company_id = _company_id_from(body) or _company_id_from(request.args)
    if not company_id:
        return error("Se requiere company_id", 400)
    _role, denied = require_company_permission(company_id, 'docs.view')
    if denied:
        return denied
    ventas_service.get_or_init_company_context(company_id)
    to_email = body.get('email')
    try:
        res = mail_service.send_document_email(
            company_id=company_id,
            document_id=document_id,
            to_email=to_email,
            actor=get_jwt_identity() or ''
        )
    except mail_service.MailError as e:
        return error(str(e), 400)
    return success({'data': res}, message=f"Comprobante enviado exitosamente a {res.get('destinatario')}")


@ventas_bp.route('/api/ventas/documents/<document_id>/xml', methods=['GET'])
@auth_required
@handle_errors
def documents_download_xml(document_id):
    company_id = _company_id_from(request.args)
    if not company_id:
        return error("Se requiere el parámetro company_id", 400)
    _role, denied = require_company_permission(company_id, 'docs.view')
    if denied:
        return denied
    ventas_service.get_or_init_company_context(company_id)
    try:
        filename, xml_str = emission_service.download_document_xml(company_id, document_id)
    except emission_service.EmissionError as e:
        return error(str(e), 404)
    return Response(
        xml_str,
        mimetype='application/xml',
        headers={'Content-Disposition': f'attachment; filename="{filename}"'}
    )


@ventas_bp.route('/api/ventas/documents/<document_id>/pdf', methods=['GET'])
@auth_required
@handle_errors
def documents_download_pdf(document_id):
    company_id = _company_id_from(request.args)
    if not company_id:
        return error("Se requiere el parámetro company_id", 400)
    _role, denied = require_company_permission(company_id, 'docs.view')
    if denied:
        return denied
    ventas_service.get_or_init_company_context(company_id)
    try:
        filename, pdf_bytes = emission_service.download_document_pdf(company_id, document_id)
    except emission_service.EmissionError as e:
        return error(str(e), 404)
    return Response(
        pdf_bytes,
        mimetype='application/pdf',
        headers={'Content-Disposition': f'inline; filename="{filename}"'}
    )


# ══════════════════════════════════════════════════════════════════════════════
# FASE 6: GESTIÓN COMERCIAL (COTIZACIONES, PEDIDOS, KARDEX, POS)
# ══════════════════════════════════════════════════════════════════════════════

# ── Cotizaciones / Proformas ──────────────────────────────────────────────────
@ventas_bp.route('/api/ventas/quotations', methods=['POST'])
@auth_required
@handle_errors
def quotations_create():
    body = _body()
    company_id = _company_id_from(body)
    if not company_id:
        return error("Se requiere company_id", 400)
    _role, denied = require_company_permission(company_id, 'sales.create')
    if denied:
        return denied
    ventas_service.get_or_init_company_context(company_id)
    try:
        q = gestion_service.create_quotation(company_id, body, get_jwt_identity() or '')
    except gestion_service.GestionComercialError as e:
        return error(str(e), 400)
    return success({'data': q}, message=f"Cotización {q.get('numero_cotizacion')} creada exitosamente")


@ventas_bp.route('/api/ventas/quotations', methods=['GET'])
@auth_required
@handle_errors
def quotations_list():
    company_id = _company_id_from(request.args)
    if not company_id:
        return error("Se requiere el parámetro company_id", 400)
    _role, denied = require_company_permission(company_id, 'docs.view')
    if denied:
        return denied
    ventas_service.get_or_init_company_context(company_id)
    res = gestion_service.list_quotations(
        company_id=company_id,
        q=request.args.get('q', ''),
        estado=request.args.get('estado', ''),
        limit=request.args.get('limit', 50),
        offset=request.args.get('offset', 0)
    )
    return success({'data': res})


@ventas_bp.route('/api/ventas/quotations/<quotation_id>', methods=['GET'])
@auth_required
@handle_errors
def quotations_get(quotation_id):
    company_id = _company_id_from(request.args)
    if not company_id:
        return error("Se requiere el parámetro company_id", 400)
    _role, denied = require_company_permission(company_id, 'docs.view')
    if denied:
        return denied
    ventas_service.get_or_init_company_context(company_id)
    try:
        q = gestion_service.get_quotation(company_id, quotation_id)
    except gestion_service.GestionComercialError as e:
        return error(str(e), 404)
    return success({'data': q})


@ventas_bp.route('/api/ventas/quotations/<quotation_id>/status', methods=['PATCH'])
@auth_required
@handle_errors
def quotations_status_update(quotation_id):
    body = _body()
    company_id = _company_id_from(body) or _company_id_from(request.args)
    if not company_id:
        return error("Se requiere company_id", 400)
    _role, denied = require_company_permission(company_id, 'sales.create')
    if denied:
        return denied
    new_status = body.get('estado')
    try:
        updated = gestion_service.update_quotation_status(company_id, quotation_id, new_status, get_jwt_identity() or '')
    except gestion_service.GestionComercialError as e:
        return error(str(e), 400)
    return success({'data': updated}, message=f"Estado de cotización actualizado a '{new_status}'")


@ventas_bp.route('/api/ventas/quotations/<quotation_id>/convert-invoice', methods=['POST'])
@auth_required
@handle_errors
def quotations_convert_invoice(quotation_id):
    body = _body()
    company_id = _company_id_from(body) or _company_id_from(request.args)
    if not company_id:
        return error("Se requiere company_id", 400)
    _role, denied = require_company_permission(company_id, 'sales.create')
    if denied:
        return denied
    tipo_doc = str(body.get('tipo_documento') or '01').strip()
    try:
        res = gestion_service.convert_quotation_to_invoice(company_id, quotation_id, get_jwt_identity() or '', tipo_doc=tipo_doc)
    except Exception as e:
        return error(str(e), 400)
    return success({'data': res}, message="Cotización convertida en Comprobante Electrónico exitosamente")


# ── Pedidos de Venta ──────────────────────────────────────────────────────────
@ventas_bp.route('/api/ventas/orders', methods=['POST'])
@auth_required
@handle_errors
def orders_create():
    body = _body()
    company_id = _company_id_from(body)
    if not company_id:
        return error("Se requiere company_id", 400)
    _role, denied = require_company_permission(company_id, 'sales.create')
    if denied:
        return denied
    ventas_service.get_or_init_company_context(company_id)
    try:
        order = gestion_service.create_sales_order(company_id, body, get_jwt_identity() or '')
    except gestion_service.GestionComercialError as e:
        return error(str(e), 400)
    return success({'data': order}, message=f"Pedido {order.get('numero_pedido')} registrado exitosamente")


@ventas_bp.route('/api/ventas/orders', methods=['GET'])
@auth_required
@handle_errors
def orders_list():
    company_id = _company_id_from(request.args)
    if not company_id:
        return error("Se requiere el parámetro company_id", 400)
    _role, denied = require_company_permission(company_id, 'docs.view')
    if denied:
        return denied
    ventas_service.get_or_init_company_context(company_id)
    res = gestion_service.list_sales_orders(
        company_id=company_id,
        q=request.args.get('q', ''),
        estado=request.args.get('estado', ''),
        limit=request.args.get('limit', 50),
        offset=request.args.get('offset', 0)
    )
    return success({'data': res})


@ventas_bp.route('/api/ventas/orders/<order_id>', methods=['GET'])
@auth_required
@handle_errors
def orders_get(order_id):
    company_id = _company_id_from(request.args)
    if not company_id:
        return error("Se requiere el parámetro company_id", 400)
    _role, denied = require_company_permission(company_id, 'docs.view')
    if denied:
        return denied
    ventas_service.get_or_init_company_context(company_id)
    try:
        o = gestion_service.get_sales_order(company_id, order_id)
    except gestion_service.GestionComercialError as e:
        return error(str(e), 404)
    return success({'data': o})


@ventas_bp.route('/api/ventas/orders/<order_id>/status', methods=['PATCH'])
@auth_required
@handle_errors
def orders_status_update(order_id):
    body = _body()
    company_id = _company_id_from(body) or _company_id_from(request.args)
    if not company_id:
        return error("Se requiere company_id", 400)
    _role, denied = require_company_permission(company_id, 'sales.create')
    if denied:
        return denied
    new_status = body.get('estado')
    try:
        updated = gestion_service.update_sales_order_status(company_id, order_id, new_status, get_jwt_identity() or '')
    except gestion_service.GestionComercialError as e:
        return error(str(e), 400)
    return success({'data': updated}, message=f"Estado del pedido actualizado a '{new_status}'")


@ventas_bp.route('/api/ventas/orders/<order_id>/convert-invoice', methods=['POST'])
@auth_required
@handle_errors
def orders_convert_invoice(order_id):
    body = _body()
    company_id = _company_id_from(body) or _company_id_from(request.args)
    if not company_id:
        return error("Se requiere company_id", 400)
    _role, denied = require_company_permission(company_id, 'sales.create')
    if denied:
        return denied
    tipo_doc = str(body.get('tipo_documento') or '01').strip()
    try:
        res = gestion_service.convert_order_to_invoice(company_id, order_id, get_jwt_identity() or '', tipo_doc=tipo_doc)
    except Exception as e:
        return error(str(e), 400)
    return success({'data': res}, message="Pedido facturado exitosamente")


# ── Kardex de Inventario y Valorización ────────────────────────────────────────
@ventas_bp.route('/api/ventas/products/<product_id>/kardex', methods=['GET'])
@auth_required
@handle_errors
def products_kardex(product_id):
    company_id = _company_id_from(request.args)
    if not company_id:
        return error("Se requiere el parámetro company_id", 400)
    _role, denied = require_company_permission(company_id, 'products.view')
    if denied:
        return denied
    ventas_service.get_or_init_company_context(company_id)
    try:
        kardex = gestion_service.get_product_kardex(
            company_id=company_id,
            product_id=product_id,
            fecha_inicio=request.args.get('fecha_inicio', ''),
            fecha_fin=request.args.get('fecha_fin', '')
        )
    except gestion_service.GestionComercialError as e:
        return error(str(e), 404)
    return success({'data': kardex})


@ventas_bp.route('/api/ventas/inventory/valuation', methods=['GET'])
@auth_required
@handle_errors
def inventory_valuation():
    company_id = _company_id_from(request.args)
    if not company_id:
        return error("Se requiere el parámetro company_id", 400)
    _role, denied = require_company_permission(company_id, 'products.view')
    if denied:
        return denied
    ventas_service.get_or_init_company_context(company_id)
    val = gestion_service.get_inventory_valuation(company_id)
    return success({'data': val})


# ── Punto de Venta (POS) y Turnos de Caja ──────────────────────────────────────
@ventas_bp.route('/api/ventas/pos/shifts/open', methods=['POST'])
@auth_required
@handle_errors
def pos_shift_open():
    body = _body()
    company_id = _company_id_from(body)
    if not company_id:
        return error("Se requiere company_id", 400)
    _role, denied = require_company_permission(company_id, 'cash.manage')
    if denied:
        return denied
    ventas_service.get_or_init_company_context(company_id)
    try:
        shift = gestion_service.open_pos_shift(
            company_id=company_id,
            sucursal=body.get('codigo_sucursal', '001'),
            terminal=body.get('codigo_terminal', '00001'),
            cajero=get_jwt_identity() or '',
            monto_inicial=float(body.get('monto_inicial', 0.0))
        )
    except gestion_service.GestionComercialError as e:
        return error(str(e), 400)
    return success({'data': shift}, message="Turno de caja abierto satisfactoriamente")


@ventas_bp.route('/api/ventas/pos/shifts/active', methods=['GET'])
@auth_required
@handle_errors
def pos_shift_active():
    company_id = _company_id_from(request.args)
    if not company_id:
        return error("Se requiere el parámetro company_id", 400)
    _role, denied = require_company_permission(company_id, 'cash.manage')
    if denied:
        return denied
    ventas_service.get_or_init_company_context(company_id)
    shift = gestion_service.get_active_pos_shift(
        company_id=company_id,
        sucursal=request.args.get('codigo_sucursal', '001'),
        terminal=request.args.get('codigo_terminal', '00001'),
        cajero=get_jwt_identity() or ''
    )
    return success({'data': shift})


@ventas_bp.route('/api/ventas/pos/shifts/<shift_id>', methods=['GET'])
@auth_required
@handle_errors
def pos_shift_get(shift_id):
    company_id = _company_id_from(request.args)
    if not company_id:
        return error("Se requiere el parámetro company_id", 400)
    _role, denied = require_company_permission(company_id, 'cash.manage')
    if denied:
        return denied
    ventas_service.get_or_init_company_context(company_id)
    try:
        shift = gestion_service.get_pos_shift(company_id, shift_id)
    except gestion_service.GestionComercialError as e:
        return error(str(e), 404)
    return success({'data': shift})


@ventas_bp.route('/api/ventas/pos/shifts/<shift_id>/movements', methods=['POST'])
@auth_required
@handle_errors
def pos_movement_create(shift_id):
    body = _body()
    company_id = _company_id_from(body)
    if not company_id:
        return error("Se requiere company_id", 400)
    _role, denied = require_company_permission(company_id, 'cash.manage')
    if denied:
        return denied
    try:
        shift = gestion_service.register_pos_movement(
            company_id=company_id,
            shift_id=shift_id,
            tipo=body.get('tipo', 'ingreso'),
            monto=float(body.get('monto', 0)),
            motivo=body.get('motivo', ''),
            user=get_jwt_identity() or ''
        )
    except gestion_service.GestionComercialError as e:
        return error(str(e), 400)
    return success({'data': shift}, message="Movimiento de caja registrado exitosamente")


@ventas_bp.route('/api/ventas/pos/shifts/<shift_id>/close', methods=['POST'])
@auth_required
@handle_errors
def pos_shift_close(shift_id):
    body = _body()
    company_id = _company_id_from(body)
    if not company_id:
        return error("Se requiere company_id", 400)
    _role, denied = require_company_permission(company_id, 'cash.manage')
    if denied:
        return denied
    try:
        closed = gestion_service.close_pos_shift(
            company_id=company_id,
            shift_id=shift_id,
            efectivo_real=float(body.get('efectivo_real', 0.0)),
            observaciones=body.get('observaciones', ''),
            user=get_jwt_identity() or ''
        )
    except gestion_service.GestionComercialError as e:
        return error(str(e), 400)
    return success({'data': closed}, message="Turno de caja cerrado y arqueado exitosamente")


@ventas_bp.route('/api/ventas/pos/sales', methods=['POST'])
@auth_required
@handle_errors
def pos_quick_sale():
    body = _body()
    company_id = _company_id_from(body)
    if not company_id:
        return error("Se requiere company_id", 400)
    _role, denied = require_company_permission(company_id, 'sales.create')
    if denied:
        return denied
    shift_id = body.get('shift_id')
    if not shift_id:
        return error("Se requiere shift_id del turno activo de caja", 400)
    try:
        res = gestion_service.quick_pos_sale(
            company_id=company_id,
            shift_id=shift_id,
            sale_data=body,
            user=get_jwt_identity() or ''
        )
    except Exception as e:
        return error(str(e), 400)
    return success({'data': res}, message="Venta POS procesada exitosamente")


# ── Mapeo e Integración Contable NIIF (Fase 7) ─────────────────────────

@ventas_bp.route('/api/ventas/accounting/mapping', methods=['GET'])
@auth_required
@handle_errors
def get_accounting_mapping():
    company_id = _company_id_from(request.args)
    if not company_id:
        return error("Se requiere company_id", 400)
    _role, denied = require_company_permission(company_id, 'config.view')
    if denied:
        return denied
    mapping = contabilidad_ventas_service.get_or_create_accounting_mapping(company_id)
    return success({'data': mapping})


@ventas_bp.route('/api/ventas/accounting/mapping', methods=['PUT'])
@auth_required
@handle_errors
def update_accounting_mapping():
    body = _body()
    company_id = _company_id_from(body)
    if not company_id:
        return error("Se requiere company_id", 400)
    _role, denied = require_company_permission(company_id, 'config.contable')
    if denied:
        return denied
    updated = contabilidad_ventas_service.update_accounting_mapping(company_id, body)
    return success({'data': updated}, message="Mapeo contable actualizado exitosamente")


@ventas_bp.route('/api/ventas/accounting/catalog', methods=['GET'])
@auth_required
@handle_errors
def get_accounting_catalog():
    company_id = _company_id_from(request.args)
    if not company_id:
        return error("Se requiere company_id", 400)
    _role, denied = require_company_permission(company_id, 'config.view')
    if denied:
        return denied
    contabilidad_ventas_service.ensure_company_chart_of_accounts(company_id)
    with database.get_db() as conn:
        accounts = database.get_catalogo_cuentas(conn, company_id)
    return success({'data': accounts})


@ventas_bp.route('/api/ventas/accounting/entries', methods=['GET'])
@auth_required
@handle_errors
def get_accounting_entries():
    company_id = _company_id_from(request.args)
    if not company_id:
        return error("Se requiere company_id", 400)
    _role, denied = require_company_permission(company_id, 'docs.view')
    if denied:
        return denied
    doc_id = request.args.get('document_id')
    if not doc_id:
        return error("Se requiere document_id", 400)
    entries = contabilidad_ventas_service.get_document_journal_entries(company_id, doc_id)
    return success({'data': entries})


@ventas_bp.route('/api/ventas/accounting/generate', methods=['POST'])
@auth_required
@handle_errors
def generate_accounting_entry():
    body = _body()
    company_id = _company_id_from(body)
    if not company_id:
        return error("Se requiere company_id", 400)
    _role, denied = require_company_permission(company_id, 'config.contable')
    if denied:
        return denied
    doc_id = body.get('document_id')
    if not doc_id:
        return error("Se requiere document_id", 400)
    with database.get_db() as conn:
        doc = conn.execute(
            "SELECT * FROM electronic_documents WHERE id = ? AND company_id = ?",
            (doc_id, company_id)
        ).fetchone()
        if not doc:
            return error("Comprobante no encontrado", 404)
        doc_dict = dict(doc)

    if doc_dict.get('tipo_documento') == '03':
        res = contabilidad_ventas_service.generate_credit_note_journal_entry(company_id, doc_dict, force=True)
    else:
        res = contabilidad_ventas_service.generate_invoice_journal_entry(company_id, doc_dict, force=True)
    return success({'data': res}, message="Asiento contable procesado exitosamente")


@ventas_bp.route('/api/ventas/cxc', methods=['GET'])
@auth_required
@handle_errors
def list_accounts_receivable():
    company_id = _company_id_from(request.args)
    if not company_id:
        return error("Se requiere company_id", 400)
    _role, denied = require_company_permission(company_id, 'docs.view')
    if denied:
        return denied
    estado = request.args.get('estado', '')
    query = "SELECT * FROM accounts_receivable WHERE company_id = ?"
    params = [company_id]
    if estado:
        query += " AND estado = ?"
        params.append(estado)
    query += " ORDER BY fecha_emision DESC, fecha_vencimiento ASC"
    with database.get_db() as conn:
        rows = conn.execute(query, params).fetchall()
        items = [dict(r) for r in rows]
    return success({'data': {'items': items, 'total': len(items)}})


@ventas_bp.route('/api/ventas/cxc/<ar_id>/payment', methods=['POST'])
@auth_required
@handle_errors
def record_ar_payment(ar_id):
    body = _body()
    company_id = _company_id_from(body)
    if not company_id:
        return error("Se requiere company_id", 400)
    _role, denied = require_company_permission(company_id, 'sales.create')
    if denied:
        return denied

    monto = float(body.get('monto_abono', 0.0) or 0.0)
    if monto <= 0:
        return error("Monto de abono debe ser mayor a 0", 400)

    medio_pago = body.get('medio_pago', '01')
    referencia = body.get('referencia', '')

    with database.get_db() as conn:
        ar = conn.execute(
            "SELECT * FROM accounts_receivable WHERE id = ? AND company_id = ?",
            (ar_id, company_id)
        ).fetchone()
        if not ar:
            return error("Cuenta por cobrar no encontrada", 404)

        saldo_actual = float(ar['saldo_pendiente'])
        if monto > saldo_actual + 0.01:
            return error(f"El abono ({monto}) no puede exceder el saldo pendiente ({saldo_actual})", 400)

        nuevo_abonado = round(float(ar['monto_abonado']) + monto, 2)
        nuevo_saldo = max(0.0, round(float(ar['monto_original']) - nuevo_abonado, 2))
        nuevo_estado = 'pagada' if nuevo_saldo <= 0.001 else 'parcial'

        conn.execute("""
            UPDATE accounts_receivable 
            SET monto_abonado = ?, saldo_pendiente = ?, estado = ?
            WHERE id = ? AND company_id = ?
        """, (nuevo_abonado, nuevo_saldo, nuevo_estado, ar_id, company_id))

    # Generar asiento contable de abono
    asiento_id = contabilidad_ventas_service.generate_ar_payment_journal_entry(
        company_id=company_id,
        ar_id=ar_id,
        monto_abono=monto,
        medio_pago=medio_pago,
        referencia=referencia
    )

    with database.get_db() as conn:
        updated_ar = conn.execute(
            "SELECT * FROM accounts_receivable WHERE id = ? AND company_id = ?",
            (ar_id, company_id)
        ).fetchone()

    return success({
        'data': dict(updated_ar) if updated_ar else {},
        'asiento_id': asiento_id
    }, message="Abono a Cuenta por Cobrar registrado exitosamente")


# ══════════════════════════════════════════════════════════════════════════
# FASE 8: REPORTES Y ESTADÍSTICAS COMERCIALES Y FISCALES
# ══════════════════════════════════════════════════════════════════════════

@ventas_bp.route('/api/ventas/reports/d104', methods=['GET'])
@auth_required
@handle_errors
def report_d104():
    company_id = _company_id_from(request.args)
    if not company_id:
        return error("Se requiere company_id", 400)
    _role, denied = require_company_permission(company_id, 'reports.view')
    if denied:
        return denied
    start = request.args.get('start_date')
    end = request.args.get('end_date')
    data = reportes_service.get_d104_iva_report(company_id, start, end)
    return success({'data': data})


@ventas_bp.route('/api/ventas/reports/sales', methods=['GET'])
@auth_required
@handle_errors
def report_sales():
    company_id = _company_id_from(request.args)
    if not company_id:
        return error("Se requiere company_id", 400)
    _role, denied = require_company_permission(company_id, 'reports.view')
    if denied:
        return denied
    start = request.args.get('start_date')
    end = request.args.get('end_date')
    customer_id = request.args.get('customer_id')
    medio_pago = request.args.get('medio_pago')
    condicion_venta = request.args.get('condicion_venta')
    tipo_documento = request.args.get('tipo_documento')
    data = reportes_service.get_ventas_periodo_report(
        company_id, start, end, customer_id, medio_pago, condicion_venta, tipo_documento
    )
    return success({'data': data})


@ventas_bp.route('/api/ventas/reports/profitability', methods=['GET'])
@auth_required
@handle_errors
def report_profitability():
    company_id = _company_id_from(request.args)
    if not company_id:
        return error("Se requiere company_id", 400)
    _role, denied = require_company_permission(company_id, 'reports.view')
    if denied:
        return denied
    start = request.args.get('start_date')
    end = request.args.get('end_date')
    data = reportes_service.get_utilidad_bruta_report(company_id, start, end)
    return success({'data': data})


@ventas_bp.route('/api/ventas/reports/cxc-aging', methods=['GET'])
@auth_required
@handle_errors
def report_cxc_aging():
    company_id = _company_id_from(request.args)
    if not company_id:
        return error("Se requiere company_id", 400)
    _role, denied = require_company_permission(company_id, 'reports.view')
    if denied:
        return denied
    data = reportes_service.get_antiguedad_saldos_report(company_id)
    return success({'data': data})


@ventas_bp.route('/api/ventas/reports/pos-shifts', methods=['GET'])
@auth_required
@handle_errors
def report_pos_shifts():
    company_id = _company_id_from(request.args)
    if not company_id:
        return error("Se requiere company_id", 400)
    _role, denied = require_company_permission(company_id, 'reports.view')
    if denied:
        return denied
    start = request.args.get('start_date')
    end = request.args.get('end_date')
    data = reportes_service.get_cierres_caja_report(company_id, start, end)
    return success({'data': data})


@ventas_bp.route('/api/ventas/reports/export', methods=['GET'])
@auth_required
@handle_errors
def export_report():
    from datetime import datetime
    company_id = _company_id_from(request.args)
    if not company_id:
        return error("Se requiere company_id", 400)
    _role, denied = require_company_permission(company_id, 'reports.view')
    if denied:
        return denied
    
    rep_type = request.args.get('type', 'd104')
    fmt = request.args.get('format', 'csv').lower()
    start = request.args.get('start_date')
    end = request.args.get('end_date')

    if rep_type == 'd104':
        data = reportes_service.get_d104_iva_report(company_id, start, end)
    elif rep_type == 'sales':
        data = reportes_service.get_ventas_periodo_report(company_id, start, end)
    elif rep_type == 'profitability':
        data = reportes_service.get_utilidad_bruta_report(company_id, start, end)
    elif rep_type == 'cxc_aging':
        data = reportes_service.get_antiguedad_saldos_report(company_id)
    elif rep_type == 'pos_shifts':
        data = reportes_service.get_cierres_caja_report(company_id, start, end)
    else:
        return error("Tipo de reporte no válido", 400)

    filename_base = f"reporte_{rep_type}_{company_id}_{datetime.now().strftime('%Y%m%d_%H%M')}"

    if fmt == 'csv':
        csv_str = reportes_service.export_report_to_csv(rep_type, data)
        return Response(
            csv_str,
            mimetype="text/csv; charset=utf-8",
            headers={"Content-Disposition": f"attachment; filename={filename_base}.csv"}
        )
    elif fmt == 'pdf':
        pdf_bytes = reportes_service.export_report_to_pdf_bytes(rep_type, data)
        return send_file(
            io.BytesIO(pdf_bytes),
            mimetype="application/pdf",
            as_attachment=True,
            download_name=f"{filename_base}.pdf"
        )
    else:
        return error("Formato de exportación no soportado (use csv o pdf)", 400)



