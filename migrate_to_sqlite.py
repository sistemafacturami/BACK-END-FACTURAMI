"""
FACTURAMI - Migracion de JSON a SQLite
=======================================
Lee usb_database.json + facturas_queue_*.json y los inserta en facturami.db.

Uso:
  python migrate_to_sqlite.py
  python migrate_to_sqlite.py --dry-run    (solo muestra que haria, sin escribir)
"""
import json, os, sys, glob
from db import get_db, init_db, upsert_cliente, insert_transaccion, queue_add, config_set, DB_PATH

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
USB_DB = os.path.join(BASE_DIR, 'usb_database.json')
QUEUE_PATTERN = os.path.join(BASE_DIR, 'facturas_queue_*.json')

def migrate():
    dry_run = '--dry-run' in sys.argv

    print("=" * 60)
    print("  FACTURAMI - Migracion JSON -> SQLite")
    print(f"  DB destino: {DB_PATH}")
    print(f"  Modo: {'DRY RUN (sin escribir)' if dry_run else 'ESCRITURA REAL'}")
    print("=" * 60)
    print()

    # Asegurar que las tablas existan
    if not dry_run:
        init_db()

    stats = {
        'clientes': 0,
        'transacciones': 0,
        'queue': 0,
        'config': 0
    }

    # ── 1. Migrar usb_database.json ───────────────────────────────
    if not os.path.exists(USB_DB):
        print(f"[SKIP] No se encontro {USB_DB}")
    else:
        print(f"[1/3] Leyendo {USB_DB}...")
        with open(USB_DB, 'r', encoding='utf-8') as f:
            db = json.load(f)

        if not dry_run:
            with get_db() as conn:
                # Clientes
                clients = db.get('f_clients', [])
                for c in clients:
                    upsert_cliente(conn, c)
                    stats['clientes'] += 1
                    print(f"  [+] Cliente: {c.get('name', '?')} ({c.get('id', '?')})")

                # Configuracion
                if 'f_theme' in db:
                    config_set(conn, 'f_theme', db['f_theme'])
                    stats['config'] += 1
                if 'n8n_creds' in db:
                    config_set(conn, 'n8n_creds', json.dumps(db['n8n_creds']))
                    stats['config'] += 1

                # Transacciones por cliente
                for key, value in db.items():
                    if not key.startswith('f_data_') or not isinstance(value, dict):
                        continue

                    cid = key.replace('f_data_', '')
                    print(f"\n  [*] Procesando datos de cliente: {cid}")

                    for mod_name in ('ingresos', 'compras', 'gastos', 'costos', 'obras'):
                        mod_data = value.get(mod_name)
                        if not mod_data or not isinstance(mod_data, dict):
                            continue

                        rows = mod_data.get('rows', [])
                        if not rows:
                            continue

                        for row in rows:
                            insert_transaccion(conn, cid, mod_name, row, origen='migracion')
                            stats['transacciones'] += 1

                        print(f"      {mod_name}: {len(rows)} registros migrados")

                    # Activos (si existen como dict de categorias)
                    activos = value.get('activos')
                    if isinstance(activos, dict):
                        for cat, items in activos.items():
                            if isinstance(items, list):
                                for item in items:
                                    insert_transaccion(conn, cid, 'activos', item, origen='migracion')
                                    stats['transacciones'] += 1
                                if items:
                                    print(f"      activos.{cat}: {len(items)} registros migrados")
        else:
            clients = db.get('f_clients', [])
            print(f"  [DRY] Encontrados {len(clients)} clientes")
            for key, value in db.items():
                if key.startswith('f_data_') and isinstance(value, dict):
                    cid = key.replace('f_data_', '')
                    total_rows = 0
                    for mod in ('ingresos', 'compras', 'gastos', 'costos', 'obras'):
                        mod_data = value.get(mod, {})
                        if isinstance(mod_data, dict):
                            total_rows += len(mod_data.get('rows', []))
                    print(f"  [DRY] {cid}: {total_rows} transacciones")

    # ── 2. Migrar colas de facturas ──────────────────────────────
    print(f"\n[2/3] Migrando colas de facturas...")
    queue_files = glob.glob(QUEUE_PATTERN)

    if not queue_files:
        print("  [SKIP] No se encontraron archivos de cola")
    else:
        for qf in queue_files:
            fname = os.path.basename(qf)
            try:
                with open(qf, 'r', encoding='utf-8') as f:
                    items = json.load(f)
            except Exception as e:
                print(f"  [ERROR] No se pudo leer {fname}: {e}")
                continue

            if not isinstance(items, list):
                continue

            print(f"  [*] {fname}: {len(items)} facturas")

            if not dry_run:
                with get_db() as conn:
                    for item in items:
                        # Reusar el ID original si existe
                        original_id = item.get('_id')
                        q_id = queue_add(conn, item)

                        # Si tenia un estado diferente, actualizarlo
                        estado = item.get('estado', 'pendiente')
                        if estado != 'pendiente':
                            conn.execute(
                                "UPDATE facturas_queue SET estado=? WHERE id=?",
                                (estado, q_id)
                            )

                        stats['queue'] += 1

    # ── 3. Resumen ───────────────────────────────────────────────
    print(f"\n[3/3] Verificando integridad...")
    if not dry_run:
        with get_db() as conn:
            c_count = conn.execute("SELECT COUNT(*) FROM clientes").fetchone()[0]
            t_count = conn.execute("SELECT COUNT(*) FROM transacciones").fetchone()[0]
            q_count = conn.execute("SELECT COUNT(*) FROM facturas_queue").fetchone()[0]
            cfg_count = conn.execute("SELECT COUNT(*) FROM configuracion").fetchone()[0]
            print(f"  Clientes en DB:      {c_count}")
            print(f"  Transacciones en DB: {t_count}")
            print(f"  Cola facturas en DB: {q_count}")
            print(f"  Configuraciones:     {cfg_count}")

    print()
    print("=" * 60)
    if dry_run:
        print("  DRY RUN completado. No se escribio nada.")
        print("  Ejecuta sin --dry-run para migrar.")
    else:
        print("  MIGRACION COMPLETADA")
        print(f"  Clientes:      {stats['clientes']}")
        print(f"  Transacciones: {stats['transacciones']}")
        print(f"  Cola:          {stats['queue']}")
        print(f"  Config:        {stats['config']}")
        print()
        print(f"  Base de datos: {DB_PATH}")
        print(f"  Tamano: {os.path.getsize(DB_PATH) / 1024:.1f} KB")
    print("=" * 60)

if __name__ == '__main__':
    migrate()
