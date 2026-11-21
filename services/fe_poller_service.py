"""
FACTURAMI - Demonio y Servicio de Consulta Automática ante Hacienda (Poller Worker).
====================================================================================
Monitorea y sincroniza en segundo plano el estado de validación oficial de los comprobantes
electrónicos enviados al Ministerio de Hacienda de Costa Rica (de 'enviado' a 'aceptado' o 'rechazado').
Permite ejecución bajo demanda vía API, script cron y demonio en segundo plano (background thread).
"""
import logging
import os
import threading
import time

import db as database
from services.fe_emission_service import sync_hacienda_status, EmissionError

logger = logging.getLogger('facturami.fe_poller')

_poller_thread = None
_poller_stop_event = threading.Event()
_poller_lock = threading.Lock()


def poll_pending_hacienda_documents(company_id: str = None, limit: int = 50, db_path=None) -> dict:
    """
    Busca documentos en estados pendientes de confirmación ('enviado', 'recibido_hacienda', 'en_proceso', 'procesando')
    y consulta su estado oficial ante Hacienda para actualizar su XML de respuesta y estatus.
    """
    limit = max(1, min(int(limit or 50), 100))
    where = ["estado_comprobante IN ('enviado', 'recibido_hacienda', 'en_proceso', 'procesando')"]
    params = []

    if company_id:
        where.append("company_id = ?")
        params.append(str(company_id).strip())

    params.append(limit)
    sql = f"""
        SELECT id, company_id, consecutivo, clave_numerica, estado_comprobante
        FROM electronic_documents
        WHERE {' AND '.join(where)}
        ORDER BY created_at ASC
        LIMIT ?
    """

    with database.get_db(db_path) as conn:
        rows = conn.execute(sql, params).fetchall()

    consultados = 0
    aceptados = 0
    rechazados = 0
    pendientes = 0
    errores = 0
    detalles = []

    for r in rows:
        did = r['id']
        cid = r['company_id']
        consecutivo = r['consecutivo']
        consultados += 1

        try:
            doc_act = sync_hacienda_status(cid, did, actor='hacienda_poller', db_path=db_path)
            nuevo_est = doc_act.get('estado_comprobante')
            msg = doc_act.get('hacienda_mensaje', '')

            if nuevo_est == 'aceptado':
                aceptados += 1
            elif nuevo_est == 'rechazado':
                rechazados += 1
            else:
                pendientes += 1

            detalles.append({
                'id': did,
                'company_id': cid,
                'consecutivo': consecutivo,
                'estado': nuevo_est,
                'mensaje': msg
            })
        except EmissionError as e:
            errores += 1
            detalles.append({
                'id': did,
                'company_id': cid,
                'consecutivo': consecutivo,
                'estado': 'error',
                'mensaje': str(e)
            })
        except Exception as e_unexp:
            errores += 1
            detalles.append({
                'id': did,
                'company_id': cid,
                'consecutivo': consecutivo,
                'estado': 'error',
                'mensaje': f"Excepción en polling: {e_unexp}"
            })

    if consultados > 0:
        try:
            with database.get_db(db_path) as conn:
                database.insert_audit_log(
                    conn, 'ventas_hacienda_poller_batch', 'system_poller', 'batch',
                    company_id or 'all_companies',
                    {'consultados': consultados, 'aceptados': aceptados, 'rechazados': rechazados, 'errores': errores}
                )
        except Exception:
            pass

    return {
        'consultados': consultados,
        'aceptados': aceptados,
        'rechazados': rechazados,
        'pendientes': pendientes,
        'errores': errores,
        'detalles': detalles
    }


def _poller_worker_loop(interval_seconds: int = 60, db_path=None):
    """Bucle del demonio en segundo plano."""
    logger.info(f"[HACIENDA POLLER] Iniciado con intervalo de {interval_seconds}s.")
    while not _poller_stop_event.is_set():
        try:
            res = poll_pending_hacienda_documents(limit=50, db_path=db_path)
            if res['consultados'] > 0:
                logger.info(
                    f"[HACIENDA POLLER] Comprobantes procesados: {res['consultados']} "
                    f"(Aceptados: {res['aceptados']}, Rechazados: {res['rechazados']}, Pendientes: {res['pendientes']})"
                )
        except Exception as e:
            logger.warning(f"[HACIENDA POLLER] Error en ciclo de sincronización: {e}")

        # Dormir en incrementos cortos para responder rápido a stop()
        for _ in range(max(1, int(interval_seconds))):
            if _poller_stop_event.is_set():
                break
            time.sleep(1)

    logger.info("[HACIENDA POLLER] Demonio detenido limpiamente.")


def start_hacienda_poller(interval_seconds: int = 60, db_path=None):
    """Inicia el demonio en segundo plano si no está corriendo ya."""
    global _poller_thread
    with _poller_lock:
        if _poller_thread and _poller_thread.is_alive():
            return False
        _poller_stop_event.clear()
        _poller_thread = threading.Thread(
            target=_poller_worker_loop,
            args=(interval_seconds, db_path),
            daemon=True,
            name="HaciendaPollerThread"
        )
        _poller_thread.start()
        return True


def stop_hacienda_poller():
    """Detiene el demonio en segundo plano."""
    global _poller_thread
    with _poller_lock:
        _poller_stop_event.set()
        if _poller_thread and _poller_thread.is_alive():
            _poller_thread.join(timeout=3)
            _poller_thread = None
            return True
        return False


def is_poller_running() -> bool:
    """Indica si el demonio en segundo plano está activo."""
    return bool(_poller_thread and _poller_thread.is_alive())
