"""
FACTURAMI - Capa de Base de Datos SQLite
========================================
Reemplaza los archivos JSON planos con SQLite para garantizar:
- Transacciones ACID
- Concurrencia segura
- Indices para consultas rapidas
- Integridad referencial
"""
import sqlite3
import os
import uuid
import json
from datetime import datetime, date
from contextlib import contextmanager

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get('FACTURAMI_DB_PATH', os.path.join(BASE_DIR, 'facturami.db'))

# ══════════════════════════════════════════════════════════════════════════
#  Conexion y contexto
# ══════════════════════════════════════════════════════════════════════════

def get_connection(db_path=None):
    """Crea una conexion SQLite con WAL mode para mejor concurrencia."""
    path = db_path or DB_PATH
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row          # Acceso por nombre de columna
    conn.execute("PRAGMA journal_mode=WAL")  # Write-Ahead Logging
    conn.execute("PRAGMA foreign_keys=ON")   # Integridad referencial
    conn.execute("PRAGMA busy_timeout=5000") # Esperar 5s si esta bloqueado
    return conn

@contextmanager
def get_db(db_path=None):
    """Context manager para conexiones con auto-commit/rollback."""
    conn = get_connection(db_path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

# ══════════════════════════════════════════════════════════════════════════
#  Schema / Inicializacion
# ══════════════════════════════════════════════════════════════════════════

SCHEMA_SQL = """
-- Cuentas de Contadores y Organizaciones (Multi-Tenant SaaS)
CREATE TABLE IF NOT EXISTS cuentas (
    username            TEXT PRIMARY KEY,
    nombre              TEXT NOT NULL,
    email               TEXT DEFAULT '',
    telefono            TEXT DEFAULT '',
    plan_id             TEXT DEFAULT 'despacho' CHECK(plan_id IN ('emprendedor','profesional','corporativo','despacho','master')),
    limite_facturas_mes INTEGER DEFAULT 3000,
    creditos_extra      INTEGER DEFAULT 0,
    max_clientes        INTEGER DEFAULT 9999,
    activo              INTEGER DEFAULT 1 CHECK(activo IN (0,1)),
    role                TEXT DEFAULT 'contador',
    suscripcion_hasta   TEXT DEFAULT '',
    password_hash       TEXT DEFAULT '',
    contador_reset_at   TEXT DEFAULT '',
    created_at          TEXT DEFAULT (datetime('now','localtime')),
    updated_at          TEXT DEFAULT (datetime('now','localtime'))
);

-- Clientes / Empresas
CREATE TABLE IF NOT EXISTS clientes (
    id          TEXT PRIMARY KEY,
    tipo        TEXT CHECK(tipo IN ('PF','PJ')) DEFAULT 'PF',
    nombre      TEXT NOT NULL,
    telefono    TEXT DEFAULT '',
    email       TEXT DEFAULT '',
    direccion   TEXT DEFAULT '',
    actividad   TEXT DEFAULT '',
    representante TEXT DEFAULT '',
    webhook_url TEXT DEFAULT '',
    cuenta_id   TEXT DEFAULT 'facturami1',
    created_at  TEXT DEFAULT (datetime('now','localtime')),
    updated_at  TEXT DEFAULT (datetime('now','localtime'))
);

-- Configuracion extendida de clientes para automatizacion n8n/Gemini/Drive/Gmail
CREATE TABLE IF NOT EXISTS clientes_config (
    cliente_id              TEXT PRIMARY KEY REFERENCES clientes(id) ON DELETE CASCADE,
    razon_social_prompt     TEXT NOT NULL,
    email_recepcion         TEXT DEFAULT '',
    gmail_label_inbox       TEXT DEFAULT 'INBOX',
    gmail_label_procesadas  TEXT DEFAULT 'PROCESADAS',
    gmail_label_error       TEXT DEFAULT 'ERROR_AI',
    drive_folder_inbox      TEXT NOT NULL,
    drive_folder_processed  TEXT NOT NULL,
    drive_folder_error      TEXT NOT NULL,
    drive_account_id        TEXT DEFAULT 'Facturami Credencial',
    limite_facturas_mes     INTEGER DEFAULT 500,
    limite_megas_almacen    INTEGER DEFAULT 1024,
    reglas_iva_json         TEXT DEFAULT '{"1":0.01,"2":0.02,"4":0.04,"8":0.08,"13":0.13}',
    categorias_custom_json  TEXT DEFAULT '[]',
    activo                  INTEGER DEFAULT 1 CHECK(activo IN (0,1)),
    created_at              TEXT DEFAULT (datetime('now','localtime')),
    updated_at              TEXT DEFAULT (datetime('now','localtime'))
);

-- Transacciones contables (unifica ingresos, compras, gastos, costos, obras)
CREATE TABLE IF NOT EXISTS transacciones (
    id          TEXT PRIMARY KEY,
    cliente_id  TEXT NOT NULL REFERENCES clientes(id) ON DELETE CASCADE,
    modulo      TEXT NOT NULL CHECK(modulo IN ('ingresos','compras','gastos','costos','obras')),
    fecha       TEXT NOT NULL,
    documento   TEXT DEFAULT '',
    tipo_doc    TEXT DEFAULT 'Contado',
    descripcion TEXT DEFAULT '',
    proveedor   TEXT DEFAULT '',
    total_factura REAL DEFAULT 0,
    monto_ns    REAL DEFAULT 0,
    monto_ex    REAL DEFAULT 0,
    monto_05    REAL DEFAULT 0,
    monto_1     REAL DEFAULT 0,
    monto_2     REAL DEFAULT 0,
    monto_4     REAL DEFAULT 0,
    monto_8     REAL DEFAULT 0,
    monto_13    REAL DEFAULT 0,
    origen      TEXT DEFAULT 'manual',
    drive_id    TEXT DEFAULT '',
    created_at  TEXT DEFAULT (datetime('now','localtime')),
    updated_at  TEXT DEFAULT (datetime('now','localtime'))
);

-- Cola de facturas (reemplaza facturas_queue_*.json)
CREATE TABLE IF NOT EXISTS facturas_queue (
    id          TEXT PRIMARY KEY,
    cliente_id  TEXT NOT NULL,
    estado      TEXT DEFAULT 'pendiente' CHECK(estado IN ('pendiente','absorbida','rechazada','error','duplicada')),
    origen      TEXT DEFAULT 'pdf_manual' CHECK(origen IN ('pdf_manual','gmail','api')),
    fecha       TEXT DEFAULT '',
    factura     TEXT DEFAULT '',
    categoria   TEXT DEFAULT '',
    proveedor   TEXT DEFAULT '',
    subtotal    TEXT DEFAULT '0,00',
    iva_1       TEXT DEFAULT '0,00',
    iva_2       TEXT DEFAULT '0,00',
    iva_4       TEXT DEFAULT '0,00',
    iva_13      TEXT DEFAULT '0,00',
    total       TEXT DEFAULT '0,00',
    notas       TEXT DEFAULT '',
    drive_id    TEXT DEFAULT '',
    intentos    INTEGER DEFAULT 0,
    error_log   TEXT DEFAULT '',
    hash_archivo TEXT DEFAULT '',
    recibida_at TEXT DEFAULT (datetime('now','localtime')),
    absorbida_at TEXT
);

-- Configuracion del sistema (reemplaza f_theme, n8n_creds, etc.)
CREATE TABLE IF NOT EXISTS configuracion (
    clave       TEXT PRIMARY KEY,
    valor       TEXT DEFAULT ''
);

-- Indices para consultas frecuentes
CREATE INDEX IF NOT EXISTS idx_tx_cliente   ON transacciones(cliente_id);
CREATE INDEX IF NOT EXISTS idx_tx_modulo    ON transacciones(modulo);
CREATE INDEX IF NOT EXISTS idx_tx_fecha     ON transacciones(fecha);
CREATE INDEX IF NOT EXISTS idx_tx_cli_mod   ON transacciones(cliente_id, modulo);
CREATE INDEX IF NOT EXISTS idx_queue_cli    ON facturas_queue(cliente_id);
CREATE INDEX IF NOT EXISTS idx_queue_estado ON facturas_queue(estado);
CREATE INDEX IF NOT EXISTS idx_queue_cli_estado ON facturas_queue(cliente_id, estado);
"""

def init_db(db_path=None):
    """Crea todas las tablas e indices si no existen y aplica migraciones incrementales."""
    with get_db(db_path) as conn:
        conn.executescript(SCHEMA_SQL)

        # Migracion incremental de columnas para facturas_queue
        cur = conn.cursor()
        cur.execute("PRAGMA table_info(facturas_queue)")
        existing_cols = {row['name'] for row in cur.fetchall()}
        
        column_defs = {
            'origen': "TEXT DEFAULT 'pdf_manual'",
            'intentos': "INTEGER DEFAULT 0",
            'error_log': "TEXT DEFAULT ''",
            'hash_archivo': "TEXT DEFAULT ''"
        }
        for col_name, col_type in column_defs.items():
            if col_name not in existing_cols:
                try:
                    conn.execute(f"ALTER TABLE facturas_queue ADD COLUMN {col_name} {col_type}")
                    print(f"[DB] Columna '{col_name}' anadida a facturas_queue.")
                except Exception as e:
                    print(f"[DB] Error agregando columna '{col_name}': {e}")

        # Crear indice sobre hash_archivo despues de asegurar la columna
        try:
            conn.execute("CREATE INDEX IF NOT EXISTS idx_queue_hash ON facturas_queue(hash_archivo)")
        except Exception as e:
            print(f"[DB] Indice idx_queue_hash error: {e}")

        # Migracion incremental de columna cuenta_id en clientes
        cur.execute("PRAGMA table_info(clientes)")
        existing_cli_cols = {row['name'] for row in cur.fetchall()}
        if 'cuenta_id' not in existing_cli_cols:
            try:
                conn.execute("ALTER TABLE clientes ADD COLUMN cuenta_id TEXT DEFAULT 'facturami1'")
                print("[DB] Columna 'cuenta_id' anadida a clientes.")
            except Exception as e:
                print(f"[DB] Error agregando columna 'cuenta_id': {e}")

        # Migracion incremental de columna password_hash en cuentas
        cur.execute("PRAGMA table_info(cuentas)")
        existing_cuenta_cols = {row['name'] for row in cur.fetchall()}
        if 'password_hash' not in existing_cuenta_cols:
            try:
                conn.execute("ALTER TABLE cuentas ADD COLUMN password_hash TEXT DEFAULT ''")
                print("[DB] Columna 'password_hash' anadida a cuentas.")
            except Exception as e:
                print(f"[DB] Error agregando columna 'password_hash': {e}")

        # Migracion: marca de tiempo del ultimo reinicio manual del contador de facturas
        if 'contador_reset_at' not in existing_cuenta_cols:
            try:
                conn.execute("ALTER TABLE cuentas ADD COLUMN contador_reset_at TEXT DEFAULT ''")
                print("[DB] Columna 'contador_reset_at' anadida a cuentas.")
            except Exception as e:
                print(f"[DB] Error agregando columna 'contador_reset_at': {e}")

        # Migracion: fecha limite de suscripcion
        if 'suscripcion_hasta' not in existing_cuenta_cols:
            try:
                conn.execute("ALTER TABLE cuentas ADD COLUMN suscripcion_hasta TEXT DEFAULT ''")
                print("[DB] Columna 'suscripcion_hasta' anadida a cuentas.")
            except Exception as e:
                print(f"[DB] Error agregando columna 'suscripcion_hasta': {e}")

        # Seeding inicial para cuentas
        cur.execute("SELECT COUNT(*) as cnt FROM cuentas")
        if cur.fetchone()['cnt'] == 0:
            conn.execute("""
                INSERT OR IGNORE INTO cuentas 
                (username, nombre, email, plan_id, limite_facturas_mes, max_clientes, activo, role)
                VALUES ('facturami1', 'Administrador Principal', 'sistema.facturami@gmail.com', 'master', 999999, 999999, 1, 'admin')
            """)
            print("[DB] Cuenta administradora 'facturami1' inicializada.")

        # Seeding inicial para clientes_config si esta vacio
        cur.execute("SELECT COUNT(*) as cnt FROM clientes_config")
        if cur.fetchone()['cnt'] == 0:
            # Obtener clientes existentes
            clientes = conn.execute("SELECT id, nombre, email FROM clientes").fetchall()
            for c in clientes:
                cid = c['id']
                nom = c['nombre']
                mail = c['email'] or ''
                # Asignar carpetas Drive conocidas por defecto
                inbox_f = '1pGhmiNt6fAchr0ACk0dhWyIlLRVyZdK5' # Facturas Sin Procesar
                proc_f  = '19OBoRIXSdthtmuVMShgg8HuNwg4EWPV1' # Facturas Procesadas
                prompt_name = 'Contabilidad LVM' if 'vargas' in nom.lower() or cid == '504470991' else nom
                
                conn.execute("""
                    INSERT OR IGNORE INTO clientes_config 
                    (cliente_id, razon_social_prompt, email_recepcion, drive_folder_inbox, drive_folder_processed, drive_folder_error, activo)
                    VALUES (?, ?, ?, ?, ?, ?, 1)
                """, (cid, prompt_name, mail, inbox_f, proc_f, inbox_f))
                print(f"[DB] Seed configuracion automatica creada para cliente: {nom} ({cid})")

    print("[DB] Base de datos inicializada correctamente.")

# ══════════════════════════════════════════════════════════════════════════
#  CRUD: Clientes
# ══════════════════════════════════════════════════════════════════════════

def upsert_cliente(conn, client_dict, cuenta_id=None):
    """Inserta o actualiza un cliente asociado a una cuenta de contador."""
    c_owner = cuenta_id or client_dict.get('cuenta_id') or 'lvarmedi'
    conn.execute("""
        INSERT INTO clientes (id, tipo, nombre, telefono, email, direccion, actividad, representante, webhook_url, cuenta_id)
        VALUES (:id, :tipo, :nombre, :tel, :mail, :dir, :act, :rep, :webhook, :cuenta_id)
        ON CONFLICT(id) DO UPDATE SET
            tipo=excluded.tipo, nombre=excluded.nombre, telefono=excluded.telefono,
            email=excluded.email, direccion=excluded.direccion, actividad=excluded.actividad,
            representante=excluded.representante, webhook_url=excluded.webhook_url,
            cuenta_id=CASE 
                WHEN clientes.cuenta_id IS NOT NULL AND clientes.cuenta_id != '' THEN clientes.cuenta_id 
                ELSE excluded.cuenta_id 
            END,
            updated_at=datetime('now','localtime')
    """, {
        'id':        client_dict.get('id', ''),
        'tipo':      client_dict.get('type', 'PF'),
        'nombre':    client_dict.get('name', ''),
        'tel':       client_dict.get('tel', ''),
        'mail':      client_dict.get('mail', ''),
        'dir':       client_dict.get('dir', ''),
        'act':       client_dict.get('act', ''),
        'rep':       client_dict.get('rep', ''),
        'webhook':   client_dict.get('webhook', ''),
        'cuenta_id': c_owner
    })

def get_clientes(conn, cuenta_id=None):
    """Devuelve todos los clientes en formato compatible con el frontend, filtrando por cuenta."""
    if not cuenta_id:
        return []
    if cuenta_id == '__all__':
        rows = conn.execute("SELECT * FROM clientes ORDER BY nombre").fetchall()
    else:
        rows = conn.execute("SELECT * FROM clientes WHERE cuenta_id=? ORDER BY nombre", (cuenta_id,)).fetchall()
    return [dict(r) for r in rows]

def get_clientes_legacy(conn, cuenta_id=None):
    """Devuelve clientes en el formato original del frontend (f_clients) filtrados por cuenta."""
    if not cuenta_id:
        return []
    if cuenta_id == '__all__':
        rows = conn.execute("SELECT * FROM clientes ORDER BY rowid").fetchall()
    else:
        rows = conn.execute("SELECT * FROM clientes WHERE cuenta_id=? ORDER BY rowid", (cuenta_id,)).fetchall()
    return [{
        'type': r['tipo'], 'name': r['nombre'], 'id': r['id'],
        'tel': r['telefono'], 'mail': r['email'], 'dir': r['direccion'],
        'act': r['actividad'], 'rep': r['representante'],
        'webhook': r['webhook_url'], 'cuenta_id': r['cuenta_id'] if 'cuenta_id' in r.keys() else cuenta_id
    } for r in rows]

# ══════════════════════════════════════════════════════════════════════════
#  CRUD: Cuentas de Contadores (Multi-Tenant SaaS)
# ══════════════════════════════════════════════════════════════════════════

def _enrich_cuenta(c_dict):
    """Calcula dias restantes y estado de la suscripcion para una cuenta."""
    if not c_dict:
        return c_dict
    s_hasta = c_dict.get('suscripcion_hasta') or ''
    role = c_dict.get('role', 'contador')
    if role == 'admin' or not s_hasta:
        c_dict['dias_restantes'] = None
        c_dict['estado_suscripcion'] = 'permanente'
        return c_dict
    
    try:
        from datetime import datetime, date
        exp_date = datetime.strptime(str(s_hasta)[:10], '%Y-%m-%d').date()
        today = date.today()
        diff = (exp_date - today).days
        c_dict['dias_restantes'] = diff
        if diff < 0:
            c_dict['estado_suscripcion'] = 'vencido'
        elif diff <= 5:
            c_dict['estado_suscripcion'] = 'por_vencer'
        else:
            c_dict['estado_suscripcion'] = 'activo'
    except Exception:
        c_dict['dias_restantes'] = None
        c_dict['estado_suscripcion'] = 'desconocido'
    return c_dict

def get_cuentas(conn):
    """Devuelve todas las cuentas de contadores con estadisticas de consumo y suscripcion en tiempo real."""
    rows = conn.execute("""
        SELECT c.*,
            (SELECT COUNT(*) FROM clientes cl WHERE cl.cuenta_id = c.username) as total_clientes,
            (SELECT COUNT(*) FROM facturas_queue fq 
             JOIN clientes cl ON fq.cliente_id = cl.id 
             WHERE cl.cuenta_id = c.username 
             AND fq.estado = 'absorbida'
             AND strftime('%Y-%m', COALESCE(fq.absorbida_at, fq.recibida_at)) = strftime('%Y-%m', 'now')
             AND (COALESCE(c.contador_reset_at, '') = '' OR COALESCE(fq.absorbida_at, fq.recibida_at) > c.contador_reset_at)) as facturas_mes
        FROM cuentas c
        ORDER BY c.created_at DESC
    """).fetchall()
    return [_enrich_cuenta(dict(r)) for r in rows]

def get_cuenta(conn, username):
    """Obtiene una cuenta por su username."""
    row = conn.execute("""
        SELECT c.*,
            (SELECT COUNT(*) FROM clientes cl WHERE cl.cuenta_id = c.username) as total_clientes,
            (SELECT COUNT(*) FROM facturas_queue fq 
             JOIN clientes cl ON fq.cliente_id = cl.id 
             WHERE cl.cuenta_id = c.username 
             AND fq.estado = 'absorbida'
             AND strftime('%Y-%m', COALESCE(fq.absorbida_at, fq.recibida_at)) = strftime('%Y-%m', 'now')
             AND (COALESCE(c.contador_reset_at, '') = '' OR COALESCE(fq.absorbida_at, fq.recibida_at) > c.contador_reset_at)) as facturas_mes
        FROM cuentas c WHERE c.username = ?
    """, (username,)).fetchone()
    return _enrich_cuenta(dict(row)) if row else None

def reset_contador_facturas(conn, username):
    """Reinicia a 0 el contador mensual de facturas de una cuenta.
    No borra facturas: guarda la fecha/hora del reinicio y el conteo
    solo incluye facturas absorbidas despues de ese momento."""
    cur = conn.execute(
        "UPDATE cuentas SET contador_reset_at = datetime('now','localtime'), "
        "updated_at = datetime('now','localtime') WHERE username = ?",
        (username,)
    )
    return cur.rowcount > 0

def extender_suscripcion(conn, username, dias=30, fecha_fija=None):
    """
    Extiende la suscripcion de una cuenta.
    Si se especifica fecha_fija ('YYYY-MM-DD'), se fija esa fecha directamente.
    Si se especifican dias:
      - Si la fecha actual de vencimiento es futura, se le suman los dias a esa fecha.
      - Si ya vencio o esta vacia, se le suman los dias a la fecha de hoy.
    Ademas, reactiva la cuenta (activo = 1).
    """
    from datetime import datetime, date, timedelta
    row = conn.execute("SELECT suscripcion_hasta, activo FROM cuentas WHERE username = ?", (username,)).fetchone()
    if not row:
        return None
    
    today = date.today()
    if fecha_fija:
        nueva_fecha = str(fecha_fija)[:10]
    else:
        current_hasta = row['suscripcion_hasta'] or ''
        base_date = today
        if current_hasta:
            try:
                curr_date = datetime.strptime(str(current_hasta)[:10], '%Y-%m-%d').date()
                if curr_date > today:
                    base_date = curr_date
            except Exception:
                base_date = today
        nueva_fecha = (base_date + timedelta(days=int(dias))).strftime('%Y-%m-%d')
    
    conn.execute(
        "UPDATE cuentas SET suscripcion_hasta = ?, activo = 1, updated_at = datetime('now','localtime') WHERE username = ?",
        (nueva_fecha, username)
    )
    return nueva_fecha

def upsert_cuenta(conn, c_dict):
    """Inserta o actualiza una cuenta de contador."""
    pw_hash = c_dict.get('password_hash', '')
    s_hasta = c_dict.get('suscripcion_hasta', '')
    conn.execute("""
        INSERT INTO cuentas 
        (username, nombre, email, telefono, plan_id, limite_facturas_mes, creditos_extra, max_clientes, activo, role, suscripcion_hasta, password_hash)
        VALUES (:username, :nombre, :email, :tel, :plan_id, :limite, :creditos, :max_cli, :activo, :role, :suscripcion_hasta, :pw_hash)
        ON CONFLICT(username) DO UPDATE SET
            nombre=excluded.nombre, email=excluded.email, telefono=excluded.telefono,
            plan_id=excluded.plan_id, limite_facturas_mes=excluded.limite_facturas_mes,
            creditos_extra=excluded.creditos_extra, max_clientes=excluded.max_clientes,
            activo=excluded.activo, role=excluded.role,
            suscripcion_hasta=CASE 
                WHEN :has_suscripcion = 1 THEN :suscripcion_hasta
                ELSE cuentas.suscripcion_hasta 
            END,
            password_hash=CASE WHEN excluded.password_hash != '' THEN excluded.password_hash ELSE cuentas.password_hash END,
            updated_at=datetime('now','localtime')
    """, {
        'username': c_dict['username'],
        'nombre': c_dict.get('nombre') or c_dict['username'],
        'email': c_dict.get('email', ''),
        'tel': c_dict.get('telefono', ''),
        'plan_id': c_dict.get('plan_id', 'despacho'),
        'limite': int(c_dict.get('limite_facturas_mes', 3000)),
        'creditos': int(c_dict.get('creditos_extra', 0)),
        'max_cli': int(c_dict.get('max_clientes', 9999)),
        'activo': int(c_dict.get('activo', 1)),
        'role': c_dict.get('role', 'contador'),
        'suscripcion_hasta': s_hasta,
        'has_suscripcion': 1 if 'suscripcion_hasta' in c_dict else 0,
        'pw_hash': pw_hash
    })

def delete_cuenta(conn, username):
    """Elimina una cuenta de contador."""
    conn.execute("DELETE FROM cuentas WHERE username=?", (username,))

# ══════════════════════════════════════════════════════════════════════════
#  CRUD: Clientes Config (Multi-Tenant n8n / Gemini / Drive / Gmail)
# ══════════════════════════════════════════════════════════════════════════

def get_cliente_config(conn, cliente_id):
    """Devuelve la configuracion extendida de un cliente para n8n/Gemini/Drive/Gmail."""
    row = conn.execute("SELECT * FROM clientes_config WHERE cliente_id=?", (cliente_id,)).fetchone()
    if row:
        return dict(row)
    # Si no existe en clientes_config pero si en clientes, crear configuracion default
    cli = conn.execute("SELECT id, nombre, email FROM clientes WHERE id=?", (cliente_id,)).fetchone()
    if cli:
        nom = cli['nombre']
        inbox_f = '1pGhmiNt6fAchr0ACk0dhWyIlLRVyZdK5'
        proc_f  = '19OBoRIXSdthtmuVMShgg8HuNwg4EWPV1'
        prompt_name = 'Contabilidad LVM' if 'vargas' in nom.lower() or cliente_id == '504470991' else nom
        conn.execute("""
            INSERT OR IGNORE INTO clientes_config 
            (cliente_id, razon_social_prompt, email_recepcion, drive_folder_inbox, drive_folder_processed, drive_folder_error, activo)
            VALUES (?, ?, ?, ?, ?, ?, 1)
        """, (cliente_id, prompt_name, cli['email'] or '', inbox_f, proc_f, inbox_f))
        row = conn.execute("SELECT * FROM clientes_config WHERE cliente_id=?", (cliente_id,)).fetchone()
        return dict(row) if row else None
    return None

def upsert_cliente_config(conn, conf):
    """Inserta o actualiza la configuracion de automatizacion de un cliente."""
    conn.execute("""
        INSERT INTO clientes_config
        (cliente_id, razon_social_prompt, email_recepcion, gmail_label_inbox,
         gmail_label_procesadas, gmail_label_error, drive_folder_inbox,
         drive_folder_processed, drive_folder_error, drive_account_id,
         limite_facturas_mes, limite_megas_almacen, reglas_iva_json,
         categorias_custom_json, activo, updated_at)
        VALUES (:cliente_id, :razon_social_prompt, :email_recepcion, :gmail_label_inbox,
                :gmail_label_procesadas, :gmail_label_error, :drive_folder_inbox,
                :drive_folder_processed, :drive_folder_error, :drive_account_id,
                :limite_facturas_mes, :limite_megas_almacen, :reglas_iva_json,
                :categorias_custom_json, :activo, datetime('now','localtime'))
        ON CONFLICT(cliente_id) DO UPDATE SET
            razon_social_prompt=excluded.razon_social_prompt,
            email_recepcion=excluded.email_recepcion,
            gmail_label_inbox=excluded.gmail_label_inbox,
            gmail_label_procesadas=excluded.gmail_label_procesadas,
            gmail_label_error=excluded.gmail_label_error,
            drive_folder_inbox=excluded.drive_folder_inbox,
            drive_folder_processed=excluded.drive_folder_processed,
            drive_folder_error=excluded.drive_folder_error,
            drive_account_id=excluded.drive_account_id,
            limite_facturas_mes=excluded.limite_facturas_mes,
            limite_megas_almacen=excluded.limite_megas_almacen,
            reglas_iva_json=excluded.reglas_iva_json,
            categorias_custom_json=excluded.categorias_custom_json,
            activo=excluded.activo,
            updated_at=datetime('now','localtime')
    """, conf)

def resolve_client_by_email(conn, email_str, from_email=None):
    """Resuelve el cliente configurado a partir del correo destinatario, remitente o sub-alias."""
    import re

    def extract_email(s):
        if not s:
            return ''
        m = re.search(r'[\w\.\+\-]+@[\w\.\-]+', str(s))
        return m.group(0).lower() if m else str(s).strip().lower()

    candidates = []
    if email_str:
        clean_to = extract_email(email_str)
        if clean_to:
            candidates.append(clean_to)
    if from_email:
        clean_from = extract_email(from_email)
        if clean_from and clean_from not in candidates:
            candidates.append(clean_from)

    for em in candidates:
        # 1. Match directo con email_recepcion de clientes_config
        row = conn.execute("SELECT * FROM clientes_config WHERE LOWER(email_recepcion)=? AND activo=1", (em,)).fetchone()
        if row:
            return dict(row)

        # 2. Match con sub-direccionamiento tipo facturami+CLIENTEID@gmail.com
        m = re.search(r'\+([a-zA-Z0-9_\-]+)@', em)
        if m:
            alias_id = m.group(1)
            row = conn.execute("SELECT * FROM clientes_config WHERE cliente_id=? AND activo=1", (alias_id,)).fetchone()
            if row:
                return dict(row)

        # 3. Match con el correo que se coloca a la hora de crear el perfil (tabla clientes)
        row = conn.execute("SELECT c.id FROM clientes c JOIN clientes_config cc ON c.id=cc.cliente_id WHERE LOWER(c.email)=? AND cc.activo=1", (em,)).fetchone()
        if row:
            return get_cliente_config(conn, row['id'])

    # 4. Fallback por defecto al cliente principal si solo hay 1 activo
    default_row = conn.execute("SELECT * FROM clientes_config WHERE cliente_id='504470991' AND activo=1").fetchone()
    if default_row:
        return dict(default_row)

    first_row = conn.execute("SELECT * FROM clientes_config WHERE activo=1 LIMIT 1").fetchone()
    return dict(first_row) if first_row else None


# ══════════════════════════════════════════════════════════════════════════
#  CRUD: Transacciones
# ══════════════════════════════════════════════════════════════════════════

def insert_transaccion(conn, cliente_id, modulo, row_dict, origen='manual'):
    """Inserta una transaccion contable con soporte de origen de fondos y asiento NIIF."""
    tx_id = str(uuid.uuid4())
    conn.execute("""
        INSERT INTO transacciones
        (id, cliente_id, modulo, fecha, documento, tipo_doc, descripcion, proveedor,
         total_factura, monto_ns, monto_ex, monto_05, monto_1, monto_2, monto_4, monto_8, monto_13, origen, drive_id, origen_fondos, asiento_id)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        tx_id, cliente_id, modulo,
        row_dict.get('fecha', ''),
        row_dict.get('doc', ''),
        row_dict.get('tipo', 'Contado'),
        row_dict.get('l', ''),
        row_dict.get('prov', ''),
        float(row_dict.get('tf', 0) or 0),
        float(row_dict.get('ns', 0) or 0),
        float(row_dict.get('ex', 0) or 0),
        float(row_dict.get('c05', 0) or 0),
        float(row_dict.get('c1', 0) or 0),
        float(row_dict.get('c2', 0) or 0),
        float(row_dict.get('c4', 0) or 0),
        float(row_dict.get('c8', 0) or 0),
        float(row_dict.get('c13', 0) or 0),
        origen,
        row_dict.get('drive_id', ''),
        row_dict.get('origen_fondos', 'banco'),
        row_dict.get('asiento_id', '')
    ))
    return tx_id

def get_transacciones(conn, cliente_id, modulo=None):
    """Devuelve transacciones filtradas por cliente y opcionalmente por modulo."""
    if modulo:
        rows = conn.execute(
            "SELECT * FROM transacciones WHERE cliente_id=? AND modulo=? ORDER BY fecha",
            (cliente_id, modulo)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM transacciones WHERE cliente_id=? ORDER BY modulo, fecha",
            (cliente_id,)
        ).fetchall()
    return [dict(r) for r in rows]

def get_transacciones_legacy(conn, cliente_id, modulo):
    """Devuelve transacciones en formato compatible con el frontend (rows[]), preservando origen_fondos y asiento_id."""
    rows = conn.execute(
        "SELECT * FROM transacciones WHERE cliente_id=? AND modulo=? ORDER BY fecha",
        (cliente_id, modulo)
    ).fetchall()
    result = []
    for r in rows:
        keys = r.keys() if hasattr(r, 'keys') else []
        result.append({
            'fecha': r['fecha'], 'doc': r['documento'], 'tipo': r['tipo_doc'],
            'l': r['descripcion'], 'prov': r['proveedor'], 'tf': r['total_factura'],
            'ns': r['monto_ns'], 'ex': r['monto_ex'],
            'c05': r['monto_05'], 'c1': r['monto_1'], 'c2': r['monto_2'],
            'c4': r['monto_4'], 'c8': r['monto_8'], 'c13': r['monto_13'],
            'origen_fondos': r['origen_fondos'] if 'origen_fondos' in keys else 'banco',
            'asiento_id': r['asiento_id'] if 'asiento_id' in keys else ''
        })
    return result

def delete_transaccion(conn, tx_id):
    """Elimina una transaccion por ID."""
    conn.execute("DELETE FROM transacciones WHERE id=?", (tx_id,))

# ══════════════════════════════════════════════════════════════════════════
#  NIIF: Asientos Contables, Libro Diario y Catálogo de Cuentas
# ══════════════════════════════════════════════════════════════════════════

def insert_asiento(conn, asiento_dict, lineas_list):
    """
    Inserta un asiento contable en SQLite garantizando la partida doble estricta:
    sum(debito) == sum(credito) con redondeo a 2 decimales.
    Transacción atómica: se guarda completo o se rechaza.
    """
    tot_debe = round(sum(float(l.get('debito', 0) or 0) for l in lineas_list), 2)
    tot_haber = round(sum(float(l.get('credito', 0) or 0) for l in lineas_list), 2)
    
    if abs(tot_debe - tot_haber) > 0.01:
        raise ValueError(f"Asiento descuadrado: Debe ({tot_debe:.2f}) != Haber ({tot_haber:.2f}). Se rechaza por regla contable.")

    asiento_id = asiento_dict.get('id') or str(uuid.uuid4())
    cliente_id = asiento_dict['cliente_id']
    
    # Calcular correlativo si no viene dado
    num_asiento = asiento_dict.get('num_asiento')
    if not num_asiento:
        cur_max = conn.execute("SELECT COALESCE(MAX(num_asiento), 0) FROM asientos WHERE cliente_id=?", (cliente_id,)).fetchone()
        num_asiento = (cur_max[0] if cur_max else 0) + 1

    conn.execute("""
        INSERT INTO asientos 
        (id, cliente_id, num_asiento, fecha_contable, tipo_operacion, documento_origen,
         modulo_origen, concepto, total_debe, total_haber, estado, asiento_reversion_id, hash_origen)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        asiento_id, cliente_id, num_asiento,
        asiento_dict.get('fecha_contable', date.today().isoformat()),
        asiento_dict.get('tipo_operacion', 'manual'),
        asiento_dict.get('documento_origen', ''),
        asiento_dict.get('modulo_origen', 'manual'),
        asiento_dict.get('concepto', ''),
        tot_debe, tot_haber,
        asiento_dict.get('estado', 'valido'),
        asiento_dict.get('asiento_reversion_id', ''),
        asiento_dict.get('hash_origen', '')
    ))

    for idx, linea in enumerate(lineas_list):
        linea_id = linea.get('id') or str(uuid.uuid4())
        conn.execute("""
            INSERT INTO asiento_lineas
            (id, asiento_id, cuenta_codigo, cuenta_nombre, debito, credito, referencia_doc)
            VALUES (?,?,?,?,?,?,?)
        """, (
            linea_id, asiento_id,
            linea.get('cuenta_codigo', ''),
            linea.get('cuenta_nombre', ''),
            round(float(linea.get('debito', 0) or 0), 2),
            round(float(linea.get('credito', 0) or 0), 2),
            linea.get('referencia_doc', '')
        ))

    return asiento_id

def get_asientos_por_cliente(conn, cliente_id, anio=None, mes=None):
    """Retorna todos los asientos contables con sus respectivas líneas."""
    query = "SELECT * FROM asientos WHERE cliente_id=? AND estado='valido'"
    params = [cliente_id]
    if anio:
        query += " AND fecha_contable LIKE ?"
        mes_filter = f"{anio}-{str(mes).zfill(2)}%" if mes else f"{anio}%"
        params.append(mes_filter)
    query += " ORDER BY fecha_contable ASC, num_asiento ASC"
    
    asientos_rows = conn.execute(query, params).fetchall()
    asientos = []
    for a in asientos_rows:
        ad = dict(a)
        lineas = conn.execute("SELECT * FROM asiento_lineas WHERE asiento_id=? ORDER BY id ASC", (ad['id'],)).fetchall()
        ad['lineas'] = [dict(l) for l in lineas]
        asientos.append(ad)
    return asientos

def reversar_asiento(conn, asiento_id, motivo="Anulación de comprobante"):
    """
    Reversa un asiento contable creando un contrasiento exacto (invierte Debe y Haber)
    y preservando la trazabilidad completa según NIIF (los asientos no se borran).
    """
    asiento_orig = conn.execute("SELECT * FROM asientos WHERE id=?", (asiento_id,)).fetchone()
    if not asiento_orig:
        raise ValueError("Asiento no encontrado")
    if asiento_orig['estado'] == 'reversado':
        raise ValueError("El asiento ya se encuentra reversado")
        
    lineas_orig = conn.execute("SELECT * FROM asiento_lineas WHERE asiento_id=?", (asiento_id,)).fetchall()
    
    nuevo_asiento_id = str(uuid.uuid4())
    cliente_id = asiento_orig['cliente_id']
    cur_max = conn.execute("SELECT COALESCE(MAX(num_asiento), 0) FROM asientos WHERE cliente_id=?", (cliente_id,)).fetchone()
    num_asiento = (cur_max[0] if cur_max else 0) + 1
    
    # Crear asiento de reversión
    conn.execute("""
        INSERT INTO asientos 
        (id, cliente_id, num_asiento, fecha_contable, tipo_operacion, documento_origen,
         modulo_origen, concepto, total_debe, total_haber, estado, asiento_reversion_id)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        nuevo_asiento_id, cliente_id, num_asiento,
        date.today().isoformat(),
        'reversion',
        f"REV-{asiento_orig['documento_origen']}",
        asiento_orig['modulo_origen'],
        f"REVERSIÓN ASIENTO #{asiento_orig['num_asiento']}: {motivo}",
        asiento_orig['total_haber'], asiento_orig['total_debe'],
        'valido',
        asiento_id
    ))
    
    for l in lineas_orig:
        conn.execute("""
            INSERT INTO asiento_lineas
            (id, asiento_id, cuenta_codigo, cuenta_nombre, debito, credito, referencia_doc)
            VALUES (?,?,?,?,?,?,?)
        """, (
            str(uuid.uuid4()), nuevo_asiento_id,
            l['cuenta_codigo'], l['cuenta_nombre'],
            l['credito'], l['debito'], # Invertir débito y crédito
            f"REV:{l['referencia_doc']}"
        ))
        
    # Marcar original como reversado
    conn.execute("UPDATE asientos SET estado='reversado', asiento_reversion_id=? WHERE id=?", (nuevo_asiento_id, asiento_id))
    return nuevo_asiento_id

def get_catalogo_cuentas(conn, cliente_id):
    """Devuelve el catálogo de cuentas de un cliente."""
    rows = conn.execute(
        "SELECT * FROM catalogo_cuentas WHERE cliente_id=? ORDER BY codigo ASC",
        (cliente_id,)
    ).fetchall()
    return [dict(r) for r in rows]

def get_cuentas_bancarias(conn, cliente_id):
    """Devuelve las cuentas bancarias configuradas para el cliente."""
    rows = conn.execute(
        "SELECT * FROM cuentas_bancarias WHERE cliente_id=? AND activo=1 ORDER BY banco_nombre ASC",
        (cliente_id,)
    ).fetchall()
    return [dict(r) for r in rows]

# ══════════════════════════════════════════════════════════════════════════
#  CRUD: Cola de Facturas
# ══════════════════════════════════════════════════════════════════════════

def queue_add(conn, data):
    """Agrega una factura a la cola con soporte multi-origen y hash. Retorna el ID generado."""
    q_id = str(uuid.uuid4())
    origen = data.get('origen') or data.get('ORIGEN') or 'pdf_manual'
    if origen not in ('pdf_manual', 'gmail', 'api'):
        origen = 'pdf_manual'

    def clean(val, default=''):
        if val is None:
            return default
        s = str(val).strip()
        while s.startswith('='):
            s = s[1:].strip()
        return s or default

    conn.execute("""
        INSERT INTO facturas_queue
        (id, cliente_id, estado, origen, fecha, factura, categoria, proveedor,
         subtotal, iva_1, iva_2, iva_4, iva_13, total, notas, drive_id,
         intentos, error_log, hash_archivo)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        q_id,
        clean(data.get('client_id') or data.get('CLIENTE_ID'), 'default'),
        'pendiente',
        origen,
        clean(data.get('FECHA') or data.get('FECHAFACTURA')),
        clean(data.get('FACTURA') or data.get('NUMEROFACTURA')),
        clean(data.get('Categoria')),
        clean(data.get('PROVEEDOR')),
        clean(data.get('SUB TOTAL') or data.get('SUB_TOTAL'), '0,00'),
        clean(data.get('IVA 1%'), '0,00'),
        clean(data.get('IVA 2%'), '0,00'),
        clean(data.get('IVA 4%') or data.get('IVA otros'), '0,00'),
        clean(data.get('IVA 13%'), '0,00'),
        clean(data.get('TOTAL'), '0,00'),
        clean(data.get('Notas')),
        clean(data.get('DRIVE_ID') or data.get('URL')),
        int(data.get('intentos') or 0),
        str(data.get('error_log') or ''),
        clean(data.get('hash_archivo'))
    ))
    return q_id

def queue_get_pendientes(conn, cliente_id=None):
    """Devuelve facturas pendientes de un cliente de forma estrictamente aislada."""
    if cliente_id and cliente_id not in ('all', '*', ''):
        rows = conn.execute(
            "SELECT * FROM facturas_queue WHERE cliente_id=? AND estado='pendiente' ORDER BY recibida_at",
            (cliente_id,)
        ).fetchall()
    else:
        # Modo administrador: todas las pendientes
        rows = conn.execute(
            "SELECT * FROM facturas_queue WHERE estado='pendiente' ORDER BY recibida_at"
        ).fetchall()

    # Formato compatible con el frontend existente con campo origen
    return [{
        '_id': r['id'],
        '_recibida': r['recibida_at'],
        'estado': r['estado'],
        'origen': r['origen'] if 'origen' in r.keys() and r['origen'] else 'pdf_manual',
        'CLIENTE_ID': r['cliente_id'],
        'FECHA': r['fecha'],
        'FACTURA': r['factura'],
        'Categoria': r['categoria'],
        'PROVEEDOR': r['proveedor'],
        'SUB TOTAL': r['subtotal'],
        'IVA 1%': r['iva_1'],
        'IVA 2%': r['iva_2'],
        'IVA 4%': r['iva_4'],
        'IVA 13%': r['iva_13'],
        'TOTAL': r['total'],
        'Notas': r['notas'],
        'DRIVE_ID': r['drive_id']
    } for r in rows]

def queue_count_pendientes(conn, cliente_id=None):
    """Cuenta facturas pendientes de un cliente o todas si cliente_id es 'all'/'*'."""
    if cliente_id and cliente_id not in ('all', '*', ''):
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM facturas_queue WHERE cliente_id=? AND estado='pendiente'",
            (cliente_id,)
        ).fetchone()
        return row['cnt']
    else:
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM facturas_queue WHERE estado='pendiente'"
        ).fetchone()
        return row['cnt']

def queue_check_duplicado(conn, cliente_id, numero_factura, proveedor='', hash_archivo=''):
    """Verifica si una factura ya existe en la cola mediante hash SHA-256 o clave compuesta."""
    if hash_archivo:
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM facturas_queue WHERE cliente_id=? AND hash_archivo=? AND estado='pendiente'",
            (cliente_id, hash_archivo)
        ).fetchone()
        if row['cnt'] > 0:
            return True

    num_upper = (numero_factura or '').strip().upper()
    if not num_upper or num_upper in ('N/A', 'S/N', 'SIN NUMERO'):
        return False

    prov_clean = (proveedor or '').strip().upper()
    if prov_clean:
        row = conn.execute(
            """SELECT COUNT(*) as cnt FROM facturas_queue 
               WHERE cliente_id=? AND UPPER(TRIM(factura))=? AND UPPER(TRIM(proveedor))=? AND estado='pendiente'""",
            (cliente_id, num_upper, prov_clean)
        ).fetchone()
        if row['cnt'] > 0:
            return True

    row = conn.execute(
        "SELECT COUNT(*) as cnt FROM facturas_queue WHERE cliente_id=? AND UPPER(TRIM(factura))=? AND estado='pendiente'",
        (cliente_id, num_upper)
    ).fetchone()
    return row['cnt'] > 0

def queue_absorber(conn, factura_id):
    """Marca una factura como absorbida."""
    conn.execute(
        "UPDATE facturas_queue SET estado='absorbida', absorbida_at=datetime('now','localtime') WHERE id=?",
        (factura_id,)
    )

def queue_eliminar(conn, factura_id):
    """Elimina una factura de la cola."""
    conn.execute("DELETE FROM facturas_queue WHERE id=?", (factura_id,))

def queue_get_by_id(conn, factura_id):
    """Devuelve una factura de la cola por su ID."""
    row = conn.execute("SELECT * FROM facturas_queue WHERE id=?", (factura_id,)).fetchone()
    return dict(row) if row else None

# ══════════════════════════════════════════════════════════════════════════
#  CRUD: Configuracion
# ══════════════════════════════════════════════════════════════════════════

def config_get(conn, clave, default=''):
    """Obtiene un valor de configuracion."""
    row = conn.execute("SELECT valor FROM configuracion WHERE clave=?", (clave,)).fetchone()
    return row['valor'] if row else default

def config_set(conn, clave, valor):
    """Establece un valor de configuracion."""
    conn.execute(
        "INSERT INTO configuracion (clave, valor) VALUES (?,?) ON CONFLICT(clave) DO UPDATE SET valor=excluded.valor",
        (clave, valor)
    )

# ══════════════════════════════════════════════════════════════════════════
#  Sync: Pull/Push compatibles con frontend
# ══════════════════════════════════════════════════════════════════════════

def sync_pull(conn, cuenta_id=None):
    """Genera el payload completo compatible con el formato usb_database.json, filtrando por cuenta."""
    data = {}
    
    # Clientes en formato legacy
    data['f_clients'] = get_clientes_legacy(conn, cuenta_id=cuenta_id)
    
    # Tema
    data['f_theme'] = config_get(conn, 'f_theme', 'light')
    
    # n8n creds
    creds_str = config_get(conn, 'n8n_creds', '{}')
    try:
        data['n8n_creds'] = json.loads(creds_str)
    except Exception:
        data['n8n_creds'] = {}
    
    # Datos por cliente
    for client in data['f_clients']:
        cid = client['id']
        client_data = {}
        
        MODULE_META = {
            'ingresos': {'title': 'Ingresos', 'sub': 'Ventas e ingresos del periodo', 'color': '#0a7c52'},
            'compras':  {'title': 'Compras',  'sub': 'Facturas de compra del periodo', 'color': '#1a56db'},
            'gastos':   {'title': 'Gastos',   'sub': 'Gastos operativos del periodo',   'color': '#92400e'},
            'costos':   {'title': 'Costos',   'sub': 'Costos del periodo',              'color': '#7c3aed'},
            'obras':    {'title': 'Obras',    'sub': 'Obras y proyectos',               'color': '#6b7280'},
        }
        
        for mod_name, meta in MODULE_META.items():
            rows = get_transacciones_legacy(conn, cid, mod_name)
            client_data[mod_name] = {
                'title': meta['title'],
                'sub': meta['sub'],
                'color': meta['color'],
                'rows': rows
            }
        
        # Activos: estructura diferente, guardada como JSON blob
        activos_default = {'cor': {}, 'no_cor': {}, 'intan': {}, 'depre': {}}
        activos_str = config_get(conn, f'activos_{cid}', '{}')
        try:
            loaded = json.loads(activos_str)
            # Asegurar que siempre tenga las 4 claves
            for k in activos_default:
                if k not in loaded:
                    loaded[k] = {}
            client_data['activos'] = loaded
        except Exception:
            client_data['activos'] = activos_default
        
        # Pasivos y Patrimonio: guardados como JSON blob
        try:
            client_data['pasivos'] = json.loads(config_get(conn, f'pasivos_{cid}', '{"pas_cor":{},"pas_ncor":{}}'))
        except Exception:
            client_data['pasivos'] = {'pas_cor': {}, 'pas_ncor': {}}
        
        try:
            client_data['patrimonio'] = json.loads(config_get(conn, f'patrimonio_{cid}', '{"patrim":{}}'))
        except Exception:
            client_data['patrimonio'] = {'patrim': {}}
        
        # NIIF: Cuentas bancarias, catálogo y asientos
        try:
            client_data['cuentas_bancarias'] = get_cuentas_bancarias(conn, cid)
            client_data['catalogo_cuentas'] = get_catalogo_cuentas(conn, cid)
            client_data['asientos'] = get_asientos_por_cliente(conn, cid)
        except Exception as e:
            client_data['cuentas_bancarias'] = []
            client_data['catalogo_cuentas'] = []
            client_data['asientos'] = []
        
        data[f'f_data_{cid}'] = client_data
    
    return data

def sync_push(conn, data, cuenta_id=None):
    """Recibe el payload del frontend y actualiza SQLite asociando la cuenta."""
    # Clientes
    clients = data.get('f_clients', [])
    for c in clients:
        upsert_cliente(conn, c, cuenta_id=cuenta_id or c.get('cuenta_id'))
    
    # Tema
    if 'f_theme' in data:
        config_set(conn, 'f_theme', data['f_theme'])
    
    # n8n creds
    if 'n8n_creds' in data:
        config_set(conn, 'n8n_creds', json.dumps(data['n8n_creds']))
    
    # Datos de transacciones por cliente
    for key, value in data.items():
        if key.startswith('f_data_') and isinstance(value, dict):
            cid = key.replace('f_data_', '')
            
            for mod_name in ('ingresos', 'compras', 'gastos', 'costos', 'obras'):
                mod_data = value.get(mod_name)
                if not mod_data or not isinstance(mod_data, dict):
                    continue
                
                rows = mod_data.get('rows', [])
                
                # Borrar transacciones existentes de este modulo para este cliente
                conn.execute(
                    "DELETE FROM transacciones WHERE cliente_id=? AND modulo=?",
                    (cid, mod_name)
                )
                
                # Insertar las nuevas
                for row in rows:
                    insert_transaccion(conn, cid, mod_name, row, origen='sync')
            
            # Activos: guardar como JSON blob
            activos = value.get('activos')
            if activos is not None and isinstance(activos, dict):
                config_set(conn, f'activos_{cid}', json.dumps(activos, ensure_ascii=False))
            
            # Pasivos: guardar como JSON blob
            pasivos = value.get('pasivos')
            if pasivos is not None and isinstance(pasivos, dict):
                config_set(conn, f'pasivos_{cid}', json.dumps(pasivos, ensure_ascii=False))
            
            # Patrimonio: guardar como JSON blob
            patrimonio = value.get('patrimonio')
            if patrimonio is not None and isinstance(patrimonio, dict):
                config_set(conn, f'patrimonio_{cid}', json.dumps(patrimonio, ensure_ascii=False))

            # NIIF: Cuentas bancarias
            cuentas_b = value.get('cuentas_bancarias')
            if cuentas_b and isinstance(cuentas_b, list):
                for cb in cuentas_b:
                    if isinstance(cb, dict) and cb.get('banco_nombre'):
                        conn.execute("""
                            INSERT OR REPLACE INTO cuentas_bancarias 
                            (id, cliente_id, banco_nombre, numero_cuenta, moneda, cuenta_contable_codigo, saldo_inicial, activo)
                            VALUES (?,?,?,?,?,?,?,?)
                        """, (
                            cb.get('id') or str(uuid.uuid4()), cid,
                            cb['banco_nombre'], cb.get('numero_cuenta', ''),
                            cb.get('moneda', 'CRC'), cb.get('cuenta_contable_codigo', '1.1.1.02.01'),
                            float(cb.get('saldo_inicial', 0) or 0), int(cb.get('activo', 1))
                        ))

            # NIIF: Asientos contables
            asientos_in = value.get('asientos')
            if asientos_in and isinstance(asientos_in, list):
                for asnt in asientos_in:
                    if isinstance(asnt, dict) and asnt.get('lineas'):
                        aid = asnt.get('id')
                        exist = conn.execute("SELECT id FROM asientos WHERE id=?", (aid,)).fetchone() if aid else None
                        if not exist:
                            try:
                                asnt['cliente_id'] = cid
                                insert_asiento(conn, asnt, asnt['lineas'])
                            except Exception as ex:
                                print(f"[SYNC] Error insertando asiento {aid}: {ex}")

# ══════════════════════════════════════════════════════════════════════════
#  Auto-init al importar
# ══════════════════════════════════════════════════════════════════════════

# Crear tablas al primer import
init_db()
