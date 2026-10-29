#!/usr/bin/env python3
"""
FACTURAMI - Rollback V2 NIIF
Revierte las tablas creadas por la migración NIIF de forma controlada.
"""
import os
import sys
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
import sqlite3

DEFAULT_DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'facturami.db')

def rollback(db_path=DEFAULT_DB):
    print(f"[ROLLBACK NIIF V2] Conectando a {db_path}...")
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    try:
        cur.execute("DROP TABLE IF EXISTS asiento_lineas;")
        cur.execute("DROP TABLE IF EXISTS asientos;")
        cur.execute("DROP TABLE IF EXISTS cuentas_bancarias;")
        cur.execute("DROP TABLE IF EXISTS catalogo_cuentas;")
        cur.execute("DELETE FROM configuracion WHERE clave='schema_version';")
        conn.commit()
        print("✓ [ROLLBACK NIIF V2] Tablas NIIF eliminadas exitosamente.")
    except Exception as e:
        conn.rollback()
        print(f"❌ Error durante el rollback: {e}", file=sys.stderr)
        raise
    finally:
        conn.close()

if __name__ == '__main__':
    rollback()
