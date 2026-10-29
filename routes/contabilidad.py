"""
FACTURAMI - Rutas de Contabilidad NIIF y Partida Doble
Blueprint: /api/contabilidad/*
"""
from flask import Blueprint, request
from flask_jwt_extended import jwt_required
from errors import success, error, handle_errors
import services.contabilidad_service as contabilidad_service
import db as database

contabilidad_bp = Blueprint('contabilidad', __name__)

@contabilidad_bp.route('/api/contabilidad/sin-contrapartida', methods=['GET'])
@jwt_required(optional=True)
@handle_errors
def sin_contrapartida():
    """GET /api/contabilidad/sin-contrapartida?cliente_id=..."""
    cliente_id = request.args.get('cliente_id')
    if not cliente_id:
        return error("Se requiere parámetro cliente_id", 400)
    data = contabilidad_service.get_compras_sin_contrapartida(cliente_id)
    return success(data)

@contabilidad_bp.route('/api/contabilidad/reconciliar-historico', methods=['POST'])
@jwt_required(optional=True)
@handle_errors
def reconciliar():
    """POST /api/contabilidad/reconciliar-historico"""
    body = request.get_json(force=True, silent=True) or {}
    cliente_id = body.get('cliente_id')
    asignaciones = body.get('asignaciones', [])
    if not cliente_id or not asignaciones:
        return error("Se requiere cliente_id y lista de asignaciones", 400)
    res = contabilidad_service.reconciliar_historico(cliente_id, asignaciones)
    return success(res, message=f"Se procesaron {res['asientos_generados']} asientos de partida doble")

@contabilidad_bp.route('/api/contabilidad/recalcular-depreciacion', methods=['GET'])
@jwt_required(optional=True)
@handle_errors
def recalcular_depreciacion():
    """GET /api/contabilidad/recalcular-depreciacion?cliente_id=...&fecha_corte=..."""
    cliente_id = request.args.get('cliente_id')
    fecha_corte = request.args.get('fecha_corte', '2026-10-31')
    if not cliente_id:
        return error("Se requiere parámetro cliente_id", 400)
    data = contabilidad_service.simular_recalculo_depreciacion(cliente_id, fecha_corte)
    return success(data)

@contabilidad_bp.route('/api/contabilidad/asientos', methods=['GET', 'POST'])
@jwt_required(optional=True)
@handle_errors
def asientos():
    """GET/POST /api/contabilidad/asientos"""
    if request.method == 'GET':
        cliente_id = request.args.get('cliente_id')
        anio = request.args.get('anio')
        mes = request.args.get('mes')
        if not cliente_id:
            return error("Se requiere cliente_id", 400)
        with database.get_db() as conn:
            asientos_list = database.get_asientos_por_cliente(conn, cliente_id, anio, mes)
        return success({'asientos': asientos_list, 'total': len(asientos_list)})
    
    # POST
    body = request.get_json(force=True, silent=True) or {}
    asiento_dict = body.get('asiento', {})
    lineas = body.get('lineas', [])
    if not asiento_dict or not lineas:
        return error("Se requiere objeto asiento y arreglo de lineas", 400)
    
    with database.get_db() as conn:
        try:
            aid = database.insert_asiento(conn, asiento_dict, lineas)
            return success({'asiento_id': aid}, message="Asiento contable registrado exitosamente")
        except ValueError as ve:
            return error(str(ve), 422)

@contabilidad_bp.route('/api/contabilidad/asientos/<asiento_id>/reversar', methods=['POST'])
@jwt_required(optional=True)
@handle_errors
def reversar(asiento_id):
    """POST /api/contabilidad/asientos/<id>/reversar"""
    body = request.get_json(force=True, silent=True) or {}
    motivo = body.get('motivo', 'Reversión contable solicitada por el usuario')
    with database.get_db() as conn:
        try:
            rev_id = database.reversar_asiento(conn, asiento_id, motivo)
            return success({'reversion_asiento_id': rev_id}, message="Asiento reversado con trazabilidad")
        except ValueError as ve:
            return error(str(ve), 400)

