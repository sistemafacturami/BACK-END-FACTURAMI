"""
FACTURAMI - Rutas de Contabilidad NIIF y Partida Doble.
Blueprint: /api/contabilidad/*

Autorizacion (Fase 1):
  - Todos los endpoints exigen token JWT valido (@auth_required).
  - Cada operacion valida que el cliente pertenezca a la cuenta del token (deny_unless_cliente).
"""
from flask import Blueprint, request
from errors import success, error, handle_errors
from services.authz import auth_required, deny_unless_cliente
import services.contabilidad_service as contabilidad_service
import db as database

contabilidad_bp = Blueprint('contabilidad', __name__)


@contabilidad_bp.route('/api/contabilidad/sin-contrapartida', methods=['GET'])
@auth_required
@handle_errors
def sin_contrapartida():
    """GET /api/contabilidad/sin-contrapartida?cliente_id=..."""
    cliente_id = request.args.get('cliente_id')
    if not cliente_id:
        return error("Se requiere parámetro cliente_id", 400)
    denied = deny_unless_cliente(cliente_id)
    if denied:
        return denied

    data = contabilidad_service.get_compras_sin_contrapartida(cliente_id)
    return success(data)


@contabilidad_bp.route('/api/contabilidad/reconciliar-historico', methods=['POST'])
@auth_required
@handle_errors
def reconciliar():
    """POST /api/contabilidad/reconciliar-historico"""
    body = request.get_json(force=True, silent=True) or {}
    cliente_id = body.get('cliente_id')
    asignaciones = body.get('asignaciones', [])
    if not cliente_id or not asignaciones:
        return error("Se requiere cliente_id y lista de asignaciones", 400)
    denied = deny_unless_cliente(cliente_id)
    if denied:
        return denied

    res = contabilidad_service.reconciliar_historico(cliente_id, asignaciones)
    return success(res, message=f"Se procesaron {res['asientos_generados']} asientos de partida doble")


@contabilidad_bp.route('/api/contabilidad/recalcular-depreciacion', methods=['GET'])
@auth_required
@handle_errors
def recalcular_depreciacion():
    """GET /api/contabilidad/recalcular-depreciacion?cliente_id=...&fecha_corte=..."""
    cliente_id = request.args.get('cliente_id')
    fecha_corte = request.args.get('fecha_corte', '2026-10-31')
    if not cliente_id:
        return error("Se requiere parámetro cliente_id", 400)
    denied = deny_unless_cliente(cliente_id)
    if denied:
        return denied

    data = contabilidad_service.simular_recalculo_depreciacion(cliente_id, fecha_corte)
    return success(data)


@contabilidad_bp.route('/api/contabilidad/asientos', methods=['GET', 'POST'])
@auth_required
@handle_errors
def asientos():
    """GET/POST /api/contabilidad/asientos"""
    if request.method == 'GET':
        cliente_id = request.args.get('cliente_id')
        anio = request.args.get('anio')
        mes = request.args.get('mes')
        if not cliente_id:
            return error("Se requiere cliente_id", 400)
        denied = deny_unless_cliente(cliente_id)
        if denied:
            return denied

        with database.get_db() as conn:
            asientos_list = database.get_asientos_por_cliente(conn, cliente_id, anio, mes)
        return success({'asientos': asientos_list, 'total': len(asientos_list)})

    # POST
    body = request.get_json(force=True, silent=True) or {}
    asiento_dict = body.get('asiento', {})
    lineas = body.get('lineas', [])
    if not asiento_dict or not lineas:
        return error("Se requiere objeto asiento y arreglo de lineas", 400)

    cliente_id = asiento_dict.get('cliente_id')
    if not cliente_id:
        return error("El asiento requiere cliente_id", 400)
    denied = deny_unless_cliente(cliente_id)
    if denied:
        return denied

    with database.get_db() as conn:
        try:
            aid = database.insert_asiento(conn, asiento_dict, lineas)
            return success({'asiento_id': aid}, message="Asiento contable registrado exitosamente")
        except ValueError as ve:
            return error(str(ve), 422)


@contabilidad_bp.route('/api/contabilidad/asientos/<asiento_id>/reversar', methods=['POST'])
@auth_required
@handle_errors
def reversar(asiento_id):
    """POST /api/contabilidad/asientos/<id>/reversar"""
    with database.get_db() as conn:
        asnt = conn.execute("SELECT cliente_id FROM asientos WHERE id=?", (asiento_id,)).fetchone()
        if not asnt:
            return error("Asiento no encontrado", 404)
        denied = deny_unless_cliente(asnt['cliente_id'])
        if denied:
            return error("Asiento no encontrado", 404)

        body = request.get_json(force=True, silent=True) or {}
        motivo = body.get('motivo', 'Reversión contable solicitada por el usuario')
        try:
            rev_id = database.reversar_asiento(conn, asiento_id, motivo)
            return success({'reversion_asiento_id': rev_id}, message="Asiento reversado con trazabilidad")
        except ValueError as ve:
            return error(str(ve), 400)


@contabilidad_bp.route('/api/contabilidad/balance-general', methods=['GET'])
@auth_required
@handle_errors
def balance_general():
    """GET /api/contabilidad/balance-general?cliente_id=...&fecha_corte=..."""
    cliente_id = request.args.get('cliente_id')
    fecha_corte = request.args.get('fecha_corte', '2026-10-31')
    if not cliente_id:
        return error("Se requiere parámetro cliente_id", 400)
    denied = deny_unless_cliente(cliente_id)
    if denied:
        return denied

    bg = contabilidad_service.get_balance_general(cliente_id, fecha_corte)
    return success(bg)


@contabilidad_bp.route('/api/contabilidad/ajuste-balance', methods=['POST'])
@auth_required
@handle_errors
def ajuste_balance():
    """POST /api/contabilidad/ajuste-balance"""
    body = request.get_json(force=True, silent=True) or {}
    cliente_id = body.get('cliente_id')
    if not cliente_id:
        return error("Se requiere cliente_id", 400)
    denied = deny_unless_cliente(cliente_id)
    if denied:
        return denied

    try:
        res = contabilidad_service.crear_asiento_ajuste(cliente_id, body)
        return success(res, message="Asiento de ajuste registrado exitosamente")
    except ValueError as ve:
        return error(str(ve), 422)


# ── PASIVOS FINANCIEROS Y AMORTIZACIÓN DE DEUDA ───────────────────────

@contabilidad_bp.route('/api/contabilidad/pasivos/simular-tabla', methods=['POST'])
@auth_required
@handle_errors
def simular_tabla():
    """POST /api/contabilidad/pasivos/simular-tabla"""
    body = request.get_json(force=True, silent=True) or {}
    principal = float(body.get('monto_principal') or body.get('principal') or 0)
    tasa = float(body.get('tasa_anual') or 0)
    plazo = int(body.get('plazo_meses') or 12)
    periodicidad = body.get('periodicidad', 'mensual')
    sistema = body.get('sistema', 'frances')
    fecha_inicio = body.get('fecha_inicio')
    dia_pago = int(body.get('dia_pago') or 1)
    gracia = int(body.get('periodo_gracia_capital') or 0)

    tabla = contabilidad_service.calcular_tabla_amortizacion(
        principal=principal,
        tasa_anual=tasa,
        plazo_meses=plazo,
        periodicidad=periodicidad,
        sistema=sistema,
        fecha_inicio=fecha_inicio,
        dia_pago=dia_pago,
        gracia=gracia
    )
    return success(tabla)


@contabilidad_bp.route('/api/contabilidad/pasivos/registrar', methods=['POST'])
@auth_required
@handle_errors
def registrar_pasivo():
    """POST /api/contabilidad/pasivos/registrar"""
    body = request.get_json(force=True, silent=True) or {}
    cliente_id = body.get('cliente_id')
    datos = body.get('datos', {})
    if not cliente_id or not datos:
        return error("Se requiere cliente_id y objeto datos del pasivo", 400)
    denied = deny_unless_cliente(cliente_id)
    if denied:
        return denied

    res = contabilidad_service.registrar_pasivo_financiero(cliente_id, datos)
    return success(res, message="Pasivo financiero registrado exitosamente con asiento contable")


@contabilidad_bp.route('/api/contabilidad/pasivos/pago', methods=['POST'])
@auth_required
@handle_errors
def pago_pasivo():
    """POST /api/contabilidad/pasivos/pago"""
    body = request.get_json(force=True, silent=True) or {}
    cliente_id = body.get('cliente_id')
    pasivo = body.get('pasivo', {})
    datos_pago = body.get('datos_pago', {})
    if not cliente_id or not pasivo or not datos_pago:
        return error("Se requiere cliente_id, pasivo y datos_pago", 400)
    denied = deny_unless_cliente(cliente_id)
    if denied:
        return denied

    res = contabilidad_service.registrar_pago_pasivo(cliente_id, pasivo, datos_pago)
    return success(res, message="Pago de pasivo registrado exitosamente con partida doble")


@contabilidad_bp.route('/api/contabilidad/pasivos/anular-pago', methods=['POST'])
@auth_required
@handle_errors
def anular_pago():
    """POST /api/contabilidad/pasivos/anular-pago"""
    body = request.get_json(force=True, silent=True) or {}
    cliente_id = body.get('cliente_id')
    pasivo = body.get('pasivo', {})
    pago_id = body.get('pago_id')
    motivo = body.get('motivo', 'Anulación de pago de préstamo')
    if not cliente_id or not pasivo or not pago_id:
        return error("Se requiere cliente_id, pasivo y pago_id", 400)
    denied = deny_unless_cliente(cliente_id)
    if denied:
        return denied

    res = contabilidad_service.anular_pago_pasivo(cliente_id, pasivo, pago_id, motivo)
    return success(res, message="Pago anulado y asiento contable reversado con trazabilidad")


@contabilidad_bp.route('/api/contabilidad/auditoria', methods=['GET', 'POST'])
@auth_required
@handle_errors
def auditoria():
    """GET/POST /api/contabilidad/auditoria?cliente_id=...&fecha_corte=..."""
    cliente_id = request.args.get('cliente_id')
    fecha_corte = request.args.get('fecha_corte', '2026-10-31')
    if not cliente_id:
        body = request.get_json(force=True, silent=True) or {}
        cliente_id = body.get('cliente_id')
        fecha_corte = body.get('fecha_corte', fecha_corte)
    if not cliente_id:
        return error("Se requiere parámetro cliente_id", 400)
    denied = deny_unless_cliente(cliente_id)
    if denied:
        return denied

    data = contabilidad_service.auditar_configuracion_contable(cliente_id, fecha_corte)
    return success(data)
