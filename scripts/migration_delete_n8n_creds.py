"""
FACTURAMI - Migración Manual: Eliminación de n8n_creds de SQLite
==============================================================
Elimina de forma permanente la fila 'n8n_creds' de la tabla configuracion.
NOTA: Solo debe ejecutarse bajo instrucción explícita del administrador.
"""
import sys
import os
import sqlite3

def run_migration(db_path=None):
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    target_db = db_path or os.environ.get('FACTURAMI_DB_PATH') or os.path.join(base_dir, 'facturami.db')

    if not os.path.exists(target_db):
        print(f"[ERROR] Base de datos no encontrada: {target_db}")
        return 1

    conn = sqlite3.connect(target_db, timeout=10)
    try:
        with conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM configuracion WHERE clave = 'n8n_creds'")
            deleted_count = cur.rowcount
            print(f"[MIGRACION] Filas eliminadas de 'configuracion' con clave 'n8n_creds': {deleted_count}")
        return 0
    except Exception as e:
        print(f"[ERROR] Falló la migración: {e}")
        return 1
    finally:
        conn.close()

if __name__ == '__main__':
    path_arg = sys.argv[1] if len(sys.argv) > 1 else None
    sys.exit(run_migration(path_arg))

