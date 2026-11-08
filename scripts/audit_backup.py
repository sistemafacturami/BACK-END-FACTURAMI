"""
FACTURAMI - Script de Auditoria de Backup (Solo Lectura)
=========================================================
Uso seguro contra copias de seguridad de SQLite:
    python scripts/audit_backup.py [ruta_al_backup.db]

Reporta:
1. Cuentas con 'activo IS NULL' (migraciones antiguas que requieren atencion).
2. Conteo de clientes y transacciones por cuenta.
3. Tamaño real en bytes/KB/MB del payload JSON que genera db.sync_pull por cada cuenta.
4. Alerta si alguna cuenta se acerca o excede el limite de 15 MB.
"""
import sys
import os
import json
import sqlite3

# Asegurar que el directorio raíz del proyecto esté en sys.path
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

def audit_backup(db_path):
    if not os.path.exists(db_path):
        print(f"Error: archivo no encontrado: {db_path}")
        sys.exit(1)

    abs_path = os.path.abspath(db_path)
    # Apertura estricta de solo lectura a nivel del motor SQLite (URI mode=ro)
    uri_path = f"file:{abs_path}?mode=ro"
    conn = sqlite3.connect(uri_path, uri=True)
    conn.row_factory = sqlite3.Row

    try:
        print("=" * 80)
        print(f"AUDITORIA DE SEGURIDAD Y VOLUMEN DE DATOS FACTURAMI (SOLO LECTURA)")
        print(f"Base de datos analizada: {abs_path}")
        print("=" * 80)

        # 1. Analisis de cuentas y estado de activo
        cuentas = conn.execute("SELECT username, nombre, email, role, plan_id, activo FROM cuentas ORDER BY username").fetchall()
        print(f"\n1. ESTADO DE CUENTAS ({len(cuentas)} registradas):")
        null_activos = []
        inactivos = []
        for c in cuentas:
            u = c['username']
            act = c['activo']
            if act is None:
                null_activos.append(u)
                estado = "⚠️ NULL (interpretado como ACTIVO por compatibilidad)"
            elif int(act) == 1:
                estado = "✅ Activo (1)"
            else:
                inactivos.append(u)
                estado = "🛑 Suspendido/Inactivo (0)"
            print(f"   • @{u:18} | Rol: {c['role']:8} | Plan: {c['plan_id']:12} | Estado: {estado}")

        if null_activos:
            print(f"\n   [ALERTA] Se encontraron {len(null_activos)} cuenta(s) con 'activo IS NULL': {', '.join(null_activos)}")
            print("   Recomendación: ejecutar 'UPDATE cuentas SET activo=1 WHERE activo IS NULL;' en el servidor.")
        else:
            print("\n   [OK] Ninguna cuenta tiene 'activo IS NULL'.")

        # 2. Medicion del tamaño real del payload de sync_pull por cuenta
        print("\n" + "=" * 80)
        print("2. TAMAÑO REAL DEL PAYLOAD DE SINCRONIZACION (JSON SYNC PULL):")
        print(f"{'Cuenta':20} {'Clientes':10} {'Transacciones':15} {'Tamaño JSON':15} {'Estado / Límite'}")
        print("-" * 80)

        import db as database

        total_bytes_global = 0
        for c in cuentas:
            u = c['username']
            # Construir payload aislado para esta cuenta
            try:
                data = database.sync_pull(conn, cuenta_id=u, include_creds=False)
                json_str = json.dumps(data, ensure_ascii=False)
                size_bytes = len(json_str.encode('utf-8'))
                total_bytes_global += size_bytes
                size_kb = size_bytes / 1024
                size_mb = size_kb / 1024

                num_clients = len(data.get('f_clients', []))
                num_tx = 0
                for k, v in data.items():
                    if k.startswith('f_data_') and isinstance(v, dict):
                        for mod in ('ingresos', 'compras', 'gastos', 'costos', 'obras'):
                            if mod in v and 'rows' in v[mod]:
                                num_tx += len(v[mod]['rows'])

                size_display = f"{size_mb:.2f} MB" if size_mb >= 1.0 else f"{size_kb:.1f} KB"

                if size_mb >= 15.0:
                    status_limit = "🚨 EXCEDE 15 MB (HTTP 413)"
                elif size_mb >= 10.0:
                    status_limit = "⚠️ > 10 MB (Cercano a tope)"
                else:
                    status_limit = "✅ Dentro de norma (<15MB)"

                print(f"@{u:19} {num_clients:<10} {num_tx:<15} {size_display:<15} {status_limit}")
            except Exception as ex:
                print(f"@{u:19} ERROR calculando sync_pull: {ex}")

        print("-" * 80)
        total_mb = (total_bytes_global / 1024) / 1024
        print(f"Volumen total de todos los tenants sumados: {total_mb:.2f} MB ({total_bytes_global:,} bytes)")
        print("=" * 80 + "\n")

    finally:
        conn.close()

if __name__ == '__main__':
    target = sys.argv[1] if len(sys.argv) > 1 else 'facturami.db'
    audit_backup(target)
