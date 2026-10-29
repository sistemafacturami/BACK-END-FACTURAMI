#!/usr/bin/env python3
"""
FACTURAMI - Migración V2 NIIF (Partida Doble, Catálogo de Cuentas, Asientos Contables)
Migración segura, reversible e idempotente.
"""
import os
import sys
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
import sqlite3
import uuid
import datetime

DEFAULT_DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'facturami.db')

DEFAULT_CATALOG = [
    # Activo Corriente
    ("1.1.1.01", "Caja General", "activo", "D", 4, 1),
    ("1.1.1.02", "Bancos Cuentas Corrientes", "activo", "D", 3, 0),
    ("1.1.1.02.01", "Banco Nacional de Costa Rica (BNCR)", "activo", "D", 4, 1),
    ("1.1.1.02.02", "BAC Credomatic", "activo", "D", 4, 1),
    ("1.1.1.02.03", "Banco de Costa Rica (BCR)", "activo", "D", 4, 1),
    ("1.1.2.01", "Cuentas por Cobrar Clientes", "activo", "D", 4, 1),
    ("1.1.3.01", "IVA Crédito Fiscal Soportado 13%", "activo", "D", 4, 1),
    ("1.1.3.02", "IVA Crédito Fiscal Soportado 8%", "activo", "D", 4, 1),
    ("1.1.3.03", "IVA Crédito Fiscal Soportado 4%", "activo", "D", 4, 1),
    ("1.1.3.04", "IVA Crédito Fiscal Soportado 2%", "activo", "D", 4, 1),
    ("1.1.3.05", "IVA Crédito Fiscal Soportado 1%", "activo", "D", 4, 1),
    # Activo No Corriente
    ("1.2.1.01", "Vehículos", "activo", "D", 4, 1),
    ("1.2.1.02", "Maquinaria y Herramientas", "activo", "D", 4, 1),
    ("1.2.1.03", "Mobiliario y Equipo de Oficina", "activo", "D", 4, 1),
    ("1.2.1.04", "Equipo de Cómputo", "activo", "D", 4, 1),
    # Depreciación Acumulada (Compensadora de Activo)
    ("1.2.2.01", "(-) Depreciación Acumulada Vehículos", "activo", "A", 4, 1),
    ("1.2.2.02", "(-) Depreciación Acumulada Maquinaria y Herramientas", "activo", "A", 4, 1),
    ("1.2.2.03", "(-) Depreciación Acumulada Mobiliario y Equipos", "activo", "A", 4, 1),
    # Pasivo Corriente
    ("2.1.1.01", "Cuentas por Pagar Proveedores", "pasivo", "A", 4, 1),
    ("2.1.2.01", "Tarjetas de Crédito por Pagar", "pasivo", "A", 4, 1),
    ("2.1.3.01", "IVA Débito Fiscal Devengado", "pasivo", "A", 4, 1),
    ("2.1.3.02", "IVA por Pagar a la DGT", "pasivo", "A", 4, 1),
    ("2.1.3.03", "Impuesto sobre la Renta por Pagar", "pasivo", "A", 4, 1),
    # Pasivo No Corriente
    ("2.2.1.01", "Préstamos Bancarios a Largo Plazo", "pasivo", "A", 4, 1),
    # Patrimonio
    ("3.1.1.01", "Capital del Propietario (Aportes)", "patrimonio", "A", 4, 1),
    ("3.2.1.01", "(-) Retiros Personales del Propietario", "patrimonio", "D", 4, 1),
    ("3.3.1.01", "Resultados Acumulados de Períodos Anteriores", "patrimonio", "A", 4, 1),
    ("3.4.1.01", "Utilidad o Pérdida del Ejercicio", "patrimonio", "A", 4, 1),
    # Ingresos
    ("4.1.1.01", "Ingresos por Servicios y Ventas Gravadas", "ingreso", "A", 4, 1),
    ("4.1.1.02", "Ingresos Exentos", "ingreso", "A", 4, 1),
    ("4.1.1.03", "Ingresos No Sujetos", "ingreso", "A", 4, 1),
    # Costos y Gastos
    ("5.1.1.01", "Costos de Operación y Compras de Materiales", "costo", "D", 4, 1),
    ("5.2.1.01", "Servicios Públicos", "gasto", "D", 4, 1),
    ("5.2.1.02", "Mantenimiento y Reparaciones", "gasto", "D", 4, 1),
    ("5.2.1.03", "Gastos Administrativos Generales", "gasto", "D", 4, 1),
    ("5.3.1.01", "Gasto por Depreciación de Vehículos", "gasto", "D", 4, 1),
    ("5.3.1.02", "Gasto por Depreciación de Maquinaria y Herramientas", "gasto", "D", 4, 1),
]

def migrate(db_path=DEFAULT_DB):
    print(f"[MIGRACIÓN NIIF V2] Conectando a {db_path}...")
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("PRAGMA foreign_keys = ON;")

    try:
        # 1. Crear tabla catalogo_cuentas
        cur.execute("""
        CREATE TABLE IF NOT EXISTS catalogo_cuentas (
            codigo TEXT NOT NULL,
            cliente_id TEXT NOT NULL,
            nombre TEXT NOT NULL,
            tipo TEXT NOT NULL CHECK(tipo IN ('activo','pasivo','patrimonio','ingreso','costo','gasto')),
            naturaleza TEXT NOT NULL CHECK(naturaleza IN ('D','A')),
            nivel INTEGER DEFAULT 1,
            permite_movimiento INTEGER DEFAULT 1,
            created_at TEXT DEFAULT (datetime('now','localtime')),
            PRIMARY KEY (cliente_id, codigo)
        );
        """)
        print("  ✓ Tabla catalogo_cuentas lista")

        # 2. Crear tabla cuentas_bancarias
        cur.execute("""
        CREATE TABLE IF NOT EXISTS cuentas_bancarias (
            id TEXT PRIMARY KEY,
            cliente_id TEXT NOT NULL REFERENCES clientes(id) ON DELETE CASCADE,
            banco_nombre TEXT NOT NULL,
            numero_cuenta TEXT DEFAULT '',
            moneda TEXT DEFAULT 'CRC',
            cuenta_contable_codigo TEXT DEFAULT '1.1.1.02.01',
            saldo_inicial REAL DEFAULT 0,
            activo INTEGER DEFAULT 1,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        );
        """)
        print("  ✓ Tabla cuentas_bancarias lista")

        # 3. Crear tabla asientos (Libro Diario)
        cur.execute("""
        CREATE TABLE IF NOT EXISTS asientos (
            id TEXT PRIMARY KEY,
            cliente_id TEXT NOT NULL REFERENCES clientes(id) ON DELETE CASCADE,
            num_asiento INTEGER NOT NULL,
            fecha_contable TEXT NOT NULL,
            fecha_registro TEXT DEFAULT (datetime('now','localtime')),
            tipo_operacion TEXT NOT NULL,
            documento_origen TEXT DEFAULT '',
            modulo_origen TEXT DEFAULT 'manual',
            concepto TEXT NOT NULL,
            total_debe REAL NOT NULL,
            total_haber REAL NOT NULL,
            estado TEXT DEFAULT 'valido' CHECK(estado IN ('valido','reversado')),
            asiento_reversion_id TEXT DEFAULT '',
            hash_origen TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now','localtime'))
        );
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_asientos_cliente_fecha ON asientos(cliente_id, fecha_contable);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_asientos_hash ON asientos(hash_origen);")
        print("  ✓ Tabla asientos e índices listos")

        # 4. Crear tabla asiento_lineas (Detalle del Asiento)
        cur.execute("""
        CREATE TABLE IF NOT EXISTS asiento_lineas (
            id TEXT PRIMARY KEY,
            asiento_id TEXT NOT NULL REFERENCES asientos(id) ON DELETE CASCADE,
            cuenta_codigo TEXT NOT NULL,
            cuenta_nombre TEXT NOT NULL,
            debito REAL DEFAULT 0,
            credito REAL DEFAULT 0,
            referencia_doc TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now','localtime'))
        );
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_asiento_lineas_asiento ON asiento_lineas(asiento_id);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_asiento_lineas_cuenta ON asiento_lineas(cuenta_codigo);")
        print("  ✓ Tabla asiento_lineas e índices listos")

        # 5. Agregar columnas a transacciones si no existen
        cur.execute("PRAGMA table_info(transacciones);")
        cols = [c[1] for c in cur.fetchall()]
        if 'origen_fondos' not in cols:
            cur.execute("ALTER TABLE transacciones ADD COLUMN origen_fondos TEXT DEFAULT 'banco';")
            print("  ✓ Columna origen_fondos añadida a transacciones")
        if 'asiento_id' not in cols:
            cur.execute("ALTER TABLE transacciones ADD COLUMN asiento_id TEXT DEFAULT '';")
            print("  ✓ Columna asiento_id añadida a transacciones")

        # 6. Sembrar catálogo inicial para todos los clientes existentes
        cur.execute("SELECT id FROM clientes")
        clientes = [row[0] for row in cur.fetchall()]
        print(f"  → Sembrando catálogo contable en {len(clientes)} clientes...")
        for cid in clientes:
            for item in DEFAULT_CATALOG:
                cur.execute("""
                INSERT OR IGNORE INTO catalogo_cuentas 
                (codigo, cliente_id, nombre, tipo, naturaleza, nivel, permite_movimiento)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (item[0], cid, item[1], item[2], item[3], item[4], item[5]))
            
            # Asegurar cuenta bancaria por defecto (BNCR) si no tiene ninguna
            cur.execute("SELECT COUNT(*) FROM cuentas_bancarias WHERE cliente_id=?", (cid,))
            if cur.fetchone()[0] == 0:
                cur.execute("""
                INSERT INTO cuentas_bancarias 
                (id, cliente_id, banco_nombre, numero_cuenta, moneda, cuenta_contable_codigo, saldo_inicial, activo)
                VALUES (?, ?, ?, ?, ?, ?, ?, 1)
                """, (str(uuid.uuid4()), cid, "Banco Nacional de Costa Rica (BNCR)", "CR05015100010010000000", "CRC", "1.1.1.02.01", 0.0))

        # 7. Actualizar versión en configuracion
        cur.execute("INSERT OR REPLACE INTO configuracion (clave, valor) VALUES ('schema_version', '2.0.0-niif');")
        conn.commit()
        print("✓ [MIGRACIÓN NIIF V2] Migración completada exitosamente.")
    except Exception as e:
        conn.rollback()
        print(f"❌ Error en la migración: {e}", file=sys.stderr)
        raise
    finally:
        conn.close()

if __name__ == '__main__':
    migrate()
