"""
Script de auditoria de configuracion de clientes para FACTURAMI.
Cuenta los clientes que no tienen entrada en clientes_config o que carecen
de alguna de las tres claves obligatorias:
  1. drive_folder_inbox
  2. drive_folder_processed
  3. razon_social_prompt

Uso seguro (solo lectura / conteo):
    python scripts/check_clientes_config.py [ruta_db_opcional]
"""
import sys
import sqlite3

REQUIRED_KEYS = ('drive_folder_inbox', 'drive_folder_processed', 'razon_social_prompt')

def audit_clientes_config(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        clientes = conn.execute("SELECT id, nombre, cuenta_id FROM clientes").fetchall()
        total_clientes = len(clientes)
        
        incompletos = []
        sin_config = []
        completos = []
        
        for cli in clientes:
            cid = cli['id']
            cfg = conn.execute("SELECT * FROM clientes_config WHERE cliente_id=?", (cid,)).fetchone()
            if not cfg:
                sin_config.append({'id': cid, 'nombre': cli['nombre'], 'cuenta': cli['cuenta_id'], 'faltan': list(REQUIRED_KEYS)})
            else:
                missing = [k for k in REQUIRED_KEYS if not (cfg[k] or '').strip()]
                if missing:
                    incompletos.append({'id': cid, 'nombre': cli['nombre'], 'cuenta': cli['cuenta_id'], 'faltan': missing})
                else:
                    completos.append(cid)
                    
        print(f"=== REPORTE DE AUDITORIA: CLIENTES_CONFIG ===")
        print(f"Total clientes en base de datos: {total_clientes}")
        print(f"Clientes con configuracion completa: {len(completos)}")
        print(f"Clientes SIN fila en clientes_config: {len(sin_config)}")
        print(f"Clientes con fila pero configuracion incompleta: {len(incompletos)}")
        print(f"Total clientes que fallarian en subida de facturas: {len(sin_config) + len(incompletos)}")
        
        return {
            'total': total_clientes,
            'completos': len(completos),
            'sin_config': len(sin_config),
            'incompletos': len(incompletos),
            'detalle_faltantes': sin_config + incompletos
        }
    finally:
        conn.close()

if __name__ == '__main__':
    db = sys.argv[1] if len(sys.argv) > 1 else 'facturami.db'
    audit_clientes_config(db)

