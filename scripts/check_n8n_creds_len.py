"""
FACTURAMI - Verificación de Longitud de n8n_creds (Solo Lectura)
==============================================================
Script seguro de solo lectura para inspeccionar la existencia y longitud de
la clave 'n8n_creds' en la tabla configuracion sin mostrar ni registrar su valor.
"""
import sys
import os
import sqlite3

def check_creds_length(db_path=None):
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    target_db = db_path or os.environ.get('FACTURAMI_DB_PATH') or os.path.join(base_dir, 'facturami.db')

    if not os.path.exists(target_db):
        print(f"[ERROR] Base de datos no encontrada: {target_db}")
        return 1

    conn = sqlite3.connect(f"file:{target_db}?mode=ro", uri=True)
    try:
        cur = conn.cursor()
        row = cur.execute("SELECT LENGTH(valor) FROM configuracion WHERE clave = 'n8n_creds'").fetchone()
        if row is None or row[0] is None:
            print("[INFO] La clave 'n8n_creds' no existe en la tabla 'configuracion' (0 bytes).")
            return 0

        length = row[0]
        print(f"[OK] Clave 'n8n_creds' presente. Longitud del valor: {length} caracteres (contenido protegido, no mostrado).")
        return 0
    finally:
        conn.close()

if __name__ == '__main__':
    path_arg = sys.argv[1] if len(sys.argv) > 1 else None
    sys.exit(check_creds_length(path_arg))

