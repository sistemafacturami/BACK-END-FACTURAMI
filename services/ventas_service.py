"""
FACTURAMI - Servicio del Sistema de Ventas, Facturación Electrónica y Gestión Comercial Multiempresa.
====================================================================================================
Fase 2: Inicialización de esquema comercial multiempresa, contexto automático desde perfil de cliente
y consultas reales para los 10 indicadores y 7 gráficos estadísticos del Dashboard de Ventas.
"""
import json
import uuid
from datetime import date, datetime
import db as database


VENTAS_SCHEMA_SQL = """
-- Configuración de Facturación Electrónica y Perfil Comercial por Empresa (clientes.id)
CREATE TABLE IF NOT EXISTS company_fe_settings (
    company_id                  TEXT PRIMARY KEY REFERENCES clientes(id) ON DELETE CASCADE,
    nombre_comercial            TEXT DEFAULT '',
    razon_social                TEXT DEFAULT '',
    identificacion              TEXT DEFAULT '',
    tipo_identificacion         TEXT DEFAULT '01',
    actividad_principal         TEXT DEFAULT '',
    actividades_secundarias_json TEXT DEFAULT '[]',
    provincia                   TEXT DEFAULT '1',
    canton                      TEXT DEFAULT '01',
    distrito                    TEXT DEFAULT '01',
    otras_senas                 TEXT DEFAULT '',
    telefono                    TEXT DEFAULT '',
    email                       TEXT DEFAULT '',
    sitio_web                   TEXT DEFAULT '',
    moneda_default              TEXT DEFAULT 'CRC',
    condicion_venta_default     TEXT DEFAULT '01',
    medio_pago_default          TEXT DEFAULT '01',
    plazo_credito_default       INTEGER DEFAULT 30,
    correo_envio_comprobantes   TEXT DEFAULT '',
    ambiente_hacienda           TEXT DEFAULT 'stag' CHECK(ambiente_hacienda IN ('stag','prod')),
    hacienda_username           TEXT DEFAULT '',
    hacienda_password_enc       TEXT DEFAULT '',
    cert_pin_enc                TEXT DEFAULT '',
    cert_file_enc               TEXT DEFAULT '',
    has_cert_file               INTEGER DEFAULT 0 CHECK(has_cert_file IN (0,1)),
    smtp_host                   TEXT DEFAULT '',
    smtp_port                   INTEGER DEFAULT 587,
    smtp_user                   TEXT DEFAULT '',
    smtp_password_enc           TEXT DEFAULT '',
    smtp_from_name              TEXT DEFAULT '',
    smtp_tls                    INTEGER DEFAULT 1 CHECK(smtp_tls IN (0,1)),
    smtp_enabled                INTEGER DEFAULT 0 CHECK(smtp_enabled IN (0,1)),
    created_at                  TEXT DEFAULT (datetime('now','localtime')),
    updated_at                  TEXT DEFAULT (datetime('now','localtime'))
);

-- Sucursales por Empresa
CREATE TABLE IF NOT EXISTS company_branches (
    id              TEXT PRIMARY KEY,
    company_id      TEXT NOT NULL REFERENCES clientes(id) ON DELETE CASCADE,
    codigo_sucursal TEXT NOT NULL DEFAULT '001',
    nombre          TEXT NOT NULL DEFAULT 'Casa Matriz',
    direccion       TEXT DEFAULT '',
    telefono        TEXT DEFAULT '',
    activo          INTEGER DEFAULT 1 CHECK(activo IN (0,1)),
    created_at      TEXT DEFAULT (datetime('now','localtime')),
    UNIQUE(company_id, codigo_sucursal)
);

-- Terminales y Consecutivos por Sucursal/Empresa
CREATE TABLE IF NOT EXISTS company_terminals (
    id              TEXT PRIMARY KEY,
    company_id      TEXT NOT NULL REFERENCES clientes(id) ON DELETE CASCADE,
    branch_id       TEXT NOT NULL REFERENCES company_branches(id) ON DELETE CASCADE,
    codigo_sucursal TEXT NOT NULL DEFAULT '001',
    codigo_terminal TEXT NOT NULL DEFAULT '00001',
    consec_fe       INTEGER DEFAULT 0,
    consec_te       INTEGER DEFAULT 0,
    consec_nc       INTEGER DEFAULT 0,
    consec_nd       INTEGER DEFAULT 0,
    consec_fec      INTEGER DEFAULT 0,
    consec_rep      INTEGER DEFAULT 0,
    activo          INTEGER DEFAULT 1 CHECK(activo IN (0,1)),
    created_at      TEXT DEFAULT (datetime('now','localtime')),
    UNIQUE(company_id, codigo_sucursal, codigo_terminal)
);

-- Catálogo parametrizado de Tarifas de IVA de Costa Rica (Ministerio de Hacienda)
CREATE TABLE IF NOT EXISTS tax_rates (
    codigo      TEXT PRIMARY KEY,
    descripcion TEXT NOT NULL,
    porcentaje  REAL NOT NULL,
    activo      INTEGER DEFAULT 1 CHECK(activo IN (0,1))
);

-- Directorio de Clientes Comerciales (Compradores) aislado por Empresa
CREATE TABLE IF NOT EXISTS sales_customers (
    id                      TEXT PRIMARY KEY,
    company_id              TEXT NOT NULL REFERENCES clientes(id) ON DELETE CASCADE,
    tipo_identificacion     TEXT DEFAULT '01',
    identificacion          TEXT NOT NULL,
    nombre                  TEXT NOT NULL,
    nombre_comercial        TEXT DEFAULT '',
    email                   TEXT DEFAULT '',
    telefono                TEXT DEFAULT '',
    provincia               TEXT DEFAULT '1',
    canton                  TEXT DEFAULT '01',
    distrito                TEXT DEFAULT '01',
    barrio                  TEXT DEFAULT '',
    otras_senas             TEXT DEFAULT '',
    direccion               TEXT DEFAULT '',
    actividad_economica     TEXT DEFAULT '',
    condicion_venta         TEXT DEFAULT '01',
    limite_credito          REAL DEFAULT 0,
    plazo_credito           INTEGER DEFAULT 0,
    descuento_fijo_pct      REAL DEFAULT 0,
    estado                  TEXT DEFAULT 'activo' CHECK(estado IN ('activo','inactivo')),
    created_at              TEXT DEFAULT (datetime('now','localtime')),
    updated_at              TEXT DEFAULT (datetime('now','localtime'))
);

-- Categorías de Productos y Servicios por Empresa
CREATE TABLE IF NOT EXISTS product_categories (
    id          TEXT PRIMARY KEY,
    company_id  TEXT NOT NULL REFERENCES clientes(id) ON DELETE CASCADE,
    nombre      TEXT NOT NULL,
    descripcion TEXT DEFAULT '',
    activo      INTEGER DEFAULT 1 CHECK(activo IN (0,1)),
    created_at  TEXT DEFAULT (datetime('now','localtime')),
    UNIQUE(company_id, nombre)
);

-- Catálogo de Productos y Servicios por Empresa
CREATE TABLE IF NOT EXISTS products (
    id                      TEXT PRIMARY KEY,
    company_id              TEXT NOT NULL REFERENCES clientes(id) ON DELETE CASCADE,
    codigo_interno          TEXT NOT NULL,
    codigo_barras           TEXT DEFAULT '',
    codigo_cabys            TEXT DEFAULT '',
    nombre                  TEXT NOT NULL,
    descripcion             TEXT DEFAULT '',
    tipo                    TEXT DEFAULT 'mercancia' CHECK(tipo IN ('mercancia','servicio')),
    category_id             TEXT DEFAULT '',
    categoria_nombre        TEXT DEFAULT 'General',
    unidad_medida           TEXT DEFAULT 'Unid',
    moneda                  TEXT DEFAULT 'CRC' CHECK(moneda IN ('CRC','USD')),
    precio_compra           REAL DEFAULT 0,
    precio_venta            REAL DEFAULT 0,
    precio_mayorista        REAL DEFAULT 0,
    precio_especial         REAL DEFAULT 0,
    descuento_maximo        REAL DEFAULT 0,
    margen_comercial        REAL DEFAULT 0,
    tarifa_iva_codigo       TEXT DEFAULT '08',
    tarifa_iva_porcentaje   REAL DEFAULT 13.0,
    existencia              REAL DEFAULT 0,
    stock_minimo            REAL DEFAULT 0,
    stock_maximo            REAL DEFAULT 0,
    controla_inventario     INTEGER DEFAULT 1 CHECK(controla_inventario IN (0,1)),
    estado                  TEXT DEFAULT 'activo' CHECK(estado IN ('activo','inactivo')),
    created_at              TEXT DEFAULT (datetime('now','localtime')),
    updated_at              TEXT DEFAULT (datetime('now','localtime')),
    UNIQUE(company_id, codigo_interno)
);

-- Comprobantes Electrónicos y Ventas por Empresa
CREATE TABLE IF NOT EXISTS electronic_documents (
    id                      TEXT PRIMARY KEY,
    company_id              TEXT NOT NULL REFERENCES clientes(id) ON DELETE CASCADE,
    customer_id             TEXT DEFAULT '',
    customer_nombre         TEXT DEFAULT 'Consumidor Final',
    customer_identificacion TEXT DEFAULT '',
    customer_tipo_id        TEXT DEFAULT '01',
    customer_email          TEXT DEFAULT '',
    tipo_documento          TEXT NOT NULL DEFAULT '01' CHECK(tipo_documento IN ('01','02','03','04','08','09')),
    codigo_sucursal         TEXT NOT NULL DEFAULT '001',
    codigo_terminal         TEXT NOT NULL DEFAULT '00001',
    consecutivo             TEXT NOT NULL,
    clave_numerica          TEXT DEFAULT '',
    fecha_emision           TEXT NOT NULL,
    moneda                  TEXT DEFAULT 'CRC',
    tipo_cambio             REAL DEFAULT 1.0,
    condicion_venta         TEXT DEFAULT '01',
    medio_pago              TEXT DEFAULT '01',
    plazo_credito           INTEGER DEFAULT 0,
    fecha_vencimiento       TEXT DEFAULT '',
    total_bruto             REAL DEFAULT 0,
    total_descuentos        REAL DEFAULT 0,
    subtotal                REAL DEFAULT 0,
    total_impuestos         REAL DEFAULT 0,
    otros_cargos            REAL DEFAULT 0,
    total_comprobante       REAL DEFAULT 0,
    estado_comprobante      TEXT DEFAULT 'borrador' CHECK(estado_comprobante IN (
        'borrador','pendiente_validacion','generado','firmado','pendiente_envio',
        'enviado','recibido_hacienda','en_proceso','aceptado','rechazado','error_comunicacion'
    )),
    hacienda_mensaje        TEXT DEFAULT '',
    xml_firmado             TEXT DEFAULT '',
    xml_respuesta_hacienda  TEXT DEFAULT '',
    ref_tipo_documento      TEXT DEFAULT '',
    ref_numero_clave        TEXT DEFAULT '',
    ref_fecha_emision       TEXT DEFAULT '',
    ref_codigo              TEXT DEFAULT '',
    ref_razon               TEXT DEFAULT '',
    correo_enviado_at       TEXT DEFAULT '',
    correo_destinatario     TEXT DEFAULT '',
    correo_estado           TEXT DEFAULT 'no_enviado',
    asiento_contable_id     TEXT DEFAULT '',
    usuario_creacion        TEXT DEFAULT '',
    created_at              TEXT DEFAULT (datetime('now','localtime')),
    updated_at              TEXT DEFAULT (datetime('now','localtime'))
);

-- Detalle de Líneas de Comprobantes Electrónicos / Ventas
CREATE TABLE IF NOT EXISTS electronic_document_lines (
    id                      TEXT PRIMARY KEY,
    document_id             TEXT NOT NULL REFERENCES electronic_documents(id) ON DELETE CASCADE,
    company_id              TEXT NOT NULL REFERENCES clientes(id) ON DELETE CASCADE,
    numero_linea            INTEGER NOT NULL DEFAULT 1,
    product_id              TEXT DEFAULT '',
    codigo_producto         TEXT DEFAULT '',
    codigo_cabys            TEXT DEFAULT '',
    descripcion             TEXT NOT NULL,
    categoria_nombre        TEXT DEFAULT 'General',
    cantidad                REAL NOT NULL DEFAULT 1,
    unidad_medida           TEXT DEFAULT 'Unid',
    precio_unitario         REAL NOT NULL DEFAULT 0,
    monto_descuento         REAL DEFAULT 0,
    naturaleza_descuento    TEXT DEFAULT '',
    subtotal_linea          REAL DEFAULT 0,
    base_imponible          REAL DEFAULT 0,
    tarifa_iva_codigo       TEXT DEFAULT '08',
    tarifa_iva_porcentaje   REAL DEFAULT 13.0,
    monto_impuesto          REAL DEFAULT 0,
    total_linea             REAL DEFAULT 0
);

-- Cuentas por Cobrar por Empresa
CREATE TABLE IF NOT EXISTS accounts_receivable (
    id                  TEXT PRIMARY KEY,
    company_id          TEXT NOT NULL REFERENCES clientes(id) ON DELETE CASCADE,
    document_id         TEXT NOT NULL REFERENCES electronic_documents(id) ON DELETE CASCADE,
    customer_id         TEXT DEFAULT '',
    customer_nombre     TEXT DEFAULT '',
    fecha_emision       TEXT NOT NULL,
    fecha_vencimiento   TEXT NOT NULL,
    monto_original      REAL NOT NULL DEFAULT 0,
    monto_abonado       REAL NOT NULL DEFAULT 0,
    saldo_pendiente     REAL NOT NULL DEFAULT 0,
    estado              TEXT DEFAULT 'pendiente' CHECK(estado IN ('pendiente','parcial','pagada','vencida','anulada')),
    created_at          TEXT DEFAULT (datetime('now','localtime')),
    updated_at          TEXT DEFAULT (datetime('now','localtime'))
);

-- Usuarios internos y roles por empresa (el dueño de la cuenta es admin_empresa implícito)
CREATE TABLE IF NOT EXISTS company_users (
    id          TEXT PRIMARY KEY,
    company_id  TEXT NOT NULL REFERENCES clientes(id) ON DELETE CASCADE,
    username    TEXT NOT NULL,
    role        TEXT NOT NULL CHECK(role IN ('admin_empresa','contador','vendedor','cajero')),
    activo      INTEGER DEFAULT 1 CHECK(activo IN (0,1)),
    created_by  TEXT DEFAULT '',
    created_at  TEXT DEFAULT (datetime('now','localtime')),
    UNIQUE(company_id, username)
);
CREATE INDEX IF NOT EXISTS idx_company_users_user ON company_users(username, activo);

-- Cotizaciones / Proformas por Empresa (Fase 6)
CREATE TABLE IF NOT EXISTS quotations (
    id                  TEXT PRIMARY KEY,
    company_id          TEXT NOT NULL REFERENCES clientes(id) ON DELETE CASCADE,
    customer_id         TEXT DEFAULT '',
    customer_nombre     TEXT DEFAULT 'Consumidor Final',
    customer_identificacion TEXT DEFAULT '',
    customer_email      TEXT DEFAULT '',
    numero_cotizacion   TEXT NOT NULL,
    fecha_emision       TEXT NOT NULL,
    fecha_vencimiento   TEXT NOT NULL,
    moneda              TEXT DEFAULT 'CRC' CHECK(moneda IN ('CRC','USD')),
    tipo_cambio         REAL DEFAULT 1.0,
    condicion_venta     TEXT DEFAULT '01',
    plazo_credito       INTEGER DEFAULT 0,
    subtotal            REAL DEFAULT 0,
    total_descuentos    REAL DEFAULT 0,
    total_impuestos     REAL DEFAULT 0,
    total_cotizacion    REAL DEFAULT 0,
    notas               TEXT DEFAULT '',
    estado              TEXT DEFAULT 'emitida' CHECK(estado IN ('borrador','emitida','aceptada','rechazada','facturada','vencida')),
    documento_destino_id TEXT DEFAULT '',
    usuario_creacion    TEXT DEFAULT '',
    created_at          TEXT DEFAULT (datetime('now','localtime')),
    updated_at          TEXT DEFAULT (datetime('now','localtime')),
    UNIQUE(company_id, numero_cotizacion)
);

CREATE TABLE IF NOT EXISTS quotation_lines (
    id                  TEXT PRIMARY KEY,
    quotation_id        TEXT NOT NULL REFERENCES quotations(id) ON DELETE CASCADE,
    company_id          TEXT NOT NULL REFERENCES clientes(id) ON DELETE CASCADE,
    numero_linea        INTEGER NOT NULL DEFAULT 1,
    product_id          TEXT DEFAULT '',
    codigo_interno      TEXT DEFAULT '',
    codigo_cabys        TEXT DEFAULT '',
    descripcion         TEXT NOT NULL,
    cantidad            REAL NOT NULL DEFAULT 1,
    unidad_medida       TEXT DEFAULT 'Unid',
    precio_unitario     REAL NOT NULL DEFAULT 0,
    monto_descuento     REAL DEFAULT 0,
    subtotal_linea      REAL DEFAULT 0,
    base_imponible      REAL DEFAULT 0,
    tarifa_iva_codigo   TEXT DEFAULT '08',
    tarifa_iva_porcentaje REAL DEFAULT 13.0,
    monto_impuesto      REAL DEFAULT 0,
    total_linea         REAL DEFAULT 0
);

-- Pedidos de Venta por Empresa (Fase 6)
CREATE TABLE IF NOT EXISTS sales_orders (
    id                  TEXT PRIMARY KEY,
    company_id          TEXT NOT NULL REFERENCES clientes(id) ON DELETE CASCADE,
    quotation_id        TEXT DEFAULT '',
    customer_id         TEXT DEFAULT '',
    customer_nombre     TEXT DEFAULT 'Consumidor Final',
    customer_identificacion TEXT DEFAULT '',
    customer_email      TEXT DEFAULT '',
    numero_pedido       TEXT NOT NULL,
    fecha_pedido        TEXT NOT NULL,
    fecha_entrega       TEXT DEFAULT '',
    moneda              TEXT DEFAULT 'CRC' CHECK(moneda IN ('CRC','USD')),
    tipo_cambio         REAL DEFAULT 1.0,
    condicion_venta     TEXT DEFAULT '01',
    plazo_credito       INTEGER DEFAULT 0,
    subtotal            REAL DEFAULT 0,
    total_descuentos    REAL DEFAULT 0,
    total_impuestos     REAL DEFAULT 0,
    total_pedido        REAL DEFAULT 0,
    notas               TEXT DEFAULT '',
    estado              TEXT DEFAULT 'pendiente' CHECK(estado IN ('pendiente','en_preparacion','despachado','facturado','cancelado')),
    documento_factura_id TEXT DEFAULT '',
    usuario_creacion    TEXT DEFAULT '',
    created_at          TEXT DEFAULT (datetime('now','localtime')),
    updated_at          TEXT DEFAULT (datetime('now','localtime')),
    UNIQUE(company_id, numero_pedido)
);

CREATE TABLE IF NOT EXISTS sales_order_lines (
    id                  TEXT PRIMARY KEY,
    order_id            TEXT NOT NULL REFERENCES sales_orders(id) ON DELETE CASCADE,
    company_id          TEXT NOT NULL REFERENCES clientes(id) ON DELETE CASCADE,
    numero_linea        INTEGER NOT NULL DEFAULT 1,
    product_id          TEXT DEFAULT '',
    codigo_interno      TEXT DEFAULT '',
    codigo_cabys        TEXT DEFAULT '',
    descripcion         TEXT NOT NULL,
    cantidad            REAL NOT NULL DEFAULT 1,
    unidad_medida       TEXT DEFAULT 'Unid',
    precio_unitario     REAL NOT NULL DEFAULT 0,
    monto_descuento     REAL DEFAULT 0,
    subtotal_linea      REAL DEFAULT 0,
    base_imponible      REAL DEFAULT 0,
    tarifa_iva_codigo   TEXT DEFAULT '08',
    tarifa_iva_porcentaje REAL DEFAULT 13.0,
    monto_impuesto      REAL DEFAULT 0,
    total_linea         REAL DEFAULT 0
);

-- Kardex de Movimientos de Inventario (Fase 6)
CREATE TABLE IF NOT EXISTS inventory_kardex (
    id                  TEXT PRIMARY KEY,
    company_id          TEXT NOT NULL REFERENCES clientes(id) ON DELETE CASCADE,
    product_id          TEXT NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    fecha               TEXT NOT NULL,
    tipo_movimiento     TEXT NOT NULL CHECK(tipo_movimiento IN ('entrada','salida','ajuste_positivo','ajuste_negativo','venta','devolucion')),
    documento_tipo      TEXT DEFAULT '',
    documento_ref       TEXT DEFAULT '',
    cantidad            REAL NOT NULL,
    costo_unitario      REAL NOT NULL DEFAULT 0,
    costo_total         REAL NOT NULL DEFAULT 0,
    saldo_cantidad      REAL NOT NULL DEFAULT 0,
    saldo_costo_unitario REAL NOT NULL DEFAULT 0,
    saldo_costo_total   REAL NOT NULL DEFAULT 0,
    motivo              TEXT DEFAULT '',
    usuario_creacion    TEXT DEFAULT '',
    created_at          TEXT DEFAULT (datetime('now','localtime'))
);

-- Punto de Venta: Turnos y Cajas (POS Shifts - Fase 6)
CREATE TABLE IF NOT EXISTS pos_shifts (
    id                  TEXT PRIMARY KEY,
    company_id          TEXT NOT NULL REFERENCES clientes(id) ON DELETE CASCADE,
    codigo_sucursal     TEXT NOT NULL DEFAULT '001',
    codigo_terminal     TEXT NOT NULL DEFAULT '00001',
    cajero_username     TEXT NOT NULL,
    fecha_apertura      TEXT NOT NULL,
    monto_apertura      REAL NOT NULL DEFAULT 0,
    fecha_cierre        TEXT DEFAULT '',
    total_ventas_efectivo REAL DEFAULT 0,
    total_ventas_tarjeta REAL DEFAULT 0,
    total_ventas_sinpe  REAL DEFAULT 0,
    total_ventas_otros  REAL DEFAULT 0,
    total_ingresos_extra REAL DEFAULT 0,
    total_retiros_caja  REAL DEFAULT 0,
    efectivo_esperado   REAL DEFAULT 0,
    efectivo_real       REAL DEFAULT 0,
    diferencia          REAL DEFAULT 0,
    estado              TEXT DEFAULT 'abierta' CHECK(estado IN ('abierta','cerrada')),
    observaciones_cierre TEXT DEFAULT '',
    created_at          TEXT DEFAULT (datetime('now','localtime')),
    updated_at          TEXT DEFAULT (datetime('now','localtime'))
);

-- Movimientos de Caja Chica / Retiros en Turno (Fase 6)
CREATE TABLE IF NOT EXISTS pos_movements (
    id                  TEXT PRIMARY KEY,
    shift_id            TEXT NOT NULL REFERENCES pos_shifts(id) ON DELETE CASCADE,
    company_id          TEXT NOT NULL REFERENCES clientes(id) ON DELETE CASCADE,
    tipo                TEXT NOT NULL CHECK(tipo IN ('ingreso','retiro')),
    monto               REAL NOT NULL DEFAULT 0,
    motivo              TEXT NOT NULL,
    usuario_creacion    TEXT NOT NULL,
    asiento_id          TEXT DEFAULT '',
    created_at          TEXT DEFAULT (datetime('now','localtime'))
);

-- Catálogo de Cuentas Contables (NIIF)
CREATE TABLE IF NOT EXISTS catalogo_cuentas (
    codigo              TEXT NOT NULL,
    cliente_id          TEXT NOT NULL,
    nombre              TEXT NOT NULL,
    tipo                TEXT NOT NULL CHECK(tipo IN ('activo','pasivo','patrimonio','ingreso','costo','gasto')),
    naturaleza          TEXT NOT NULL CHECK(naturaleza IN ('D','A')),
    nivel               INTEGER DEFAULT 1,
    permite_movimiento  INTEGER DEFAULT 1,
    created_at          TEXT DEFAULT (datetime('now','localtime')),
    PRIMARY KEY (cliente_id, codigo)
);

-- Cuentas Bancarias
CREATE TABLE IF NOT EXISTS cuentas_bancarias (
    id                  TEXT PRIMARY KEY,
    cliente_id          TEXT NOT NULL REFERENCES clientes(id) ON DELETE CASCADE,
    banco_nombre        TEXT NOT NULL,
    numero_cuenta       TEXT DEFAULT '',
    moneda              TEXT DEFAULT 'CRC',
    cuenta_contable_codigo TEXT DEFAULT '1.1.1.02.01',
    saldo_inicial       REAL DEFAULT 0,
    activo              INTEGER DEFAULT 1,
    created_at          TEXT DEFAULT (datetime('now','localtime'))
);

-- Asientos Contables (Libro Diario NIIF)
CREATE TABLE IF NOT EXISTS asientos (
    id                  TEXT PRIMARY KEY,
    cliente_id          TEXT NOT NULL REFERENCES clientes(id) ON DELETE CASCADE,
    num_asiento         INTEGER NOT NULL,
    fecha_contable      TEXT NOT NULL,
    fecha_registro      TEXT DEFAULT (datetime('now','localtime')),
    tipo_operacion      TEXT NOT NULL,
    documento_origen    TEXT DEFAULT '',
    modulo_origen       TEXT DEFAULT 'manual',
    concepto            TEXT NOT NULL,
    total_debe          REAL NOT NULL,
    total_haber         REAL NOT NULL,
    estado              TEXT DEFAULT 'valido' CHECK(estado IN ('valido','reversado')),
    asiento_reversion_id TEXT DEFAULT '',
    hash_origen         TEXT DEFAULT '',
    created_at          TEXT DEFAULT (datetime('now','localtime'))
);

-- Líneas de Asientos Contables (Partida Doble)
CREATE TABLE IF NOT EXISTS asiento_lineas (
    id                  TEXT PRIMARY KEY,
    asiento_id          TEXT NOT NULL REFERENCES asientos(id) ON DELETE CASCADE,
    cuenta_codigo       TEXT NOT NULL,
    cuenta_nombre       TEXT NOT NULL,
    debito              REAL DEFAULT 0,
    credito             REAL DEFAULT 0,
    referencia_doc      TEXT DEFAULT '',
    created_at          TEXT DEFAULT (datetime('now','localtime'))
);

-- Mapeo de Cuentas Contables para Ventas y Facturación Electrónica por Empresa (Fase 7)
CREATE TABLE IF NOT EXISTS company_accounting_mapping (
    company_id                  TEXT PRIMARY KEY REFERENCES clientes(id) ON DELETE CASCADE,
    cuenta_caja_efectivo        TEXT DEFAULT '1.1.1.01.01',
    cuenta_bancos_tarjeta       TEXT DEFAULT '1.1.1.02.02',
    cuenta_bancos_transferencia TEXT DEFAULT '1.1.1.02.01',
    cuenta_cxc_clientes         TEXT DEFAULT '1.1.2.01',
    cuenta_inventario           TEXT DEFAULT '1.1.3.01',
    cuenta_iva_por_pagar        TEXT DEFAULT '2.1.3.01',
    cuenta_ventas_ingresos      TEXT DEFAULT '4.1.1.01',
    cuenta_ventas_exentas       TEXT DEFAULT '4.1.1.02',
    cuenta_devoluciones_ventas  TEXT DEFAULT '4.1.2.01',
    cuenta_costo_ventas         TEXT DEFAULT '5.1.1.01',
    auto_generar_asientos       INTEGER DEFAULT 1 CHECK(auto_generar_asientos IN (0,1)),
    created_at                  TEXT DEFAULT (datetime('now','localtime')),
    updated_at                  TEXT DEFAULT (datetime('now','localtime'))
);

-- Índices para consultas rápidas multiempresa
CREATE INDEX IF NOT EXISTS idx_sales_cust_company ON sales_customers(company_id);
CREATE INDEX IF NOT EXISTS idx_products_company ON products(company_id);
CREATE INDEX IF NOT EXISTS idx_edoc_company ON electronic_documents(company_id);
CREATE INDEX IF NOT EXISTS idx_edoc_company_fecha ON electronic_documents(company_id, fecha_emision);
CREATE INDEX IF NOT EXISTS idx_edoc_company_tipo ON electronic_documents(company_id, tipo_documento);
CREATE INDEX IF NOT EXISTS idx_edoc_company_estado ON electronic_documents(company_id, estado_comprobante);
CREATE INDEX IF NOT EXISTS idx_edoc_lines_company ON electronic_document_lines(company_id, document_id);
CREATE INDEX IF NOT EXISTS idx_ar_company ON accounts_receivable(company_id, estado);
CREATE INDEX IF NOT EXISTS idx_quotations_company ON quotations(company_id, estado);
CREATE INDEX IF NOT EXISTS idx_sales_orders_company ON sales_orders(company_id, estado);
CREATE INDEX IF NOT EXISTS idx_kardex_company_prod ON inventory_kardex(company_id, product_id, fecha);
CREATE INDEX IF NOT EXISTS idx_pos_shifts_company ON pos_shifts(company_id, estado);
CREATE INDEX IF NOT EXISTS idx_pos_movements_shift ON pos_movements(shift_id);
CREATE INDEX IF NOT EXISTS idx_asientos_cliente ON asientos(cliente_id, fecha_contable);
CREATE INDEX IF NOT EXISTS idx_asiento_lineas_asiento ON asiento_lineas(asiento_id);
"""

DEFAULT_CR_TAX_RATES = [
    ('01', 'Tarifa 0% (Exento)', 0.0),
    ('02', 'Tarifa reducida 1% (Canasta Básica / Agropecuario)', 1.0),
    ('03', 'Tarifa reducida 2% (Medicamentos / Educación)', 2.0),
    ('04', 'Tarifa reducida 4% (Servicios de Salud / Boletos Aéreos)', 4.0),
    ('05', 'Transitorio 0%', 0.0),
    ('06', 'Transitorio 4% (Servicios Turísticos)', 4.0),
    ('07', 'Transitorio 8%', 8.0),
    ('08', 'Tarifa general 13%', 13.0),
    ('09', 'Tarifa reducida 0.5%', 0.5),
]

MEDIO_PAGO_LABELS = {
    '01': 'Efectivo',
    '02': 'Tarjeta',
    '03': 'Cheque',
    '04': 'Transferencia / SINPE',
    '05': 'Recaudado por terceros',
    '99': 'Otros',
}


def init_ventas_schema(db_path=None):
    """Inicializa las tablas e índices del Sistema de Ventas y parametriza tarifas IVA."""
    with database.get_db(db_path) as conn:
        conn.executescript(VENTAS_SCHEMA_SQL)
        # Migración incremental: metadatos del certificado (nunca el contenido) y configuración SMTP
        cols_fe = {r['name'] for r in conn.execute("PRAGMA table_info(company_fe_settings)").fetchall()}
        for col in ('cert_expira', 'cert_subject'):
            if col not in cols_fe:
                conn.execute(f"ALTER TABLE company_fe_settings ADD COLUMN {col} TEXT DEFAULT ''")
        for col, ctype in (('smtp_host', "TEXT DEFAULT ''"), ('smtp_port', "INTEGER DEFAULT 587"),
                           ('smtp_user', "TEXT DEFAULT ''"), ('smtp_password_enc', "TEXT DEFAULT ''"),
                           ('smtp_from_name', "TEXT DEFAULT ''"), ('smtp_tls', "INTEGER DEFAULT 1"),
                           ('smtp_enabled', "INTEGER DEFAULT 0")):
            if col not in cols_fe:
                conn.execute(f"ALTER TABLE company_fe_settings ADD COLUMN {col} {ctype}")

        # Migración incremental: campos comerciales de clientes
        cols_cust = {r['name'] for r in conn.execute("PRAGMA table_info(sales_customers)").fetchall()}
        for col, ctype in (('nombre_comercial', "TEXT DEFAULT ''"), ('barrio', "TEXT DEFAULT ''"),
                           ('otras_senas', "TEXT DEFAULT ''"), ('descuento_fijo_pct', "REAL DEFAULT 0")):
            if col not in cols_cust:
                conn.execute(f"ALTER TABLE sales_customers ADD COLUMN {col} {ctype}")

        # Migración incremental: campos de catálogo de productos
        cols_prod = {r['name'] for r in conn.execute("PRAGMA table_info(products)").fetchall()}
        for col, ctype in (('tipo', "TEXT DEFAULT 'mercancia'"), ('moneda', "TEXT DEFAULT 'CRC'"),
                           ('stock_maximo', "REAL DEFAULT 0")):
            if col not in cols_prod:
                conn.execute(f"ALTER TABLE products ADD COLUMN {col} {ctype}")

        # Migración incremental: campos de referencia de comprobantes, punto de venta y correo
        cols_edoc = {r['name'] for r in conn.execute("PRAGMA table_info(electronic_documents)").fetchall()}
        for col in ('ref_tipo_documento', 'ref_numero_clave', 'ref_fecha_emision', 'ref_codigo', 'ref_razon', 'pos_shift_id', 'asiento_id', 'asiento_costo_id'):
            if col not in cols_edoc:
                conn.execute(f"ALTER TABLE electronic_documents ADD COLUMN {col} TEXT DEFAULT ''")
        for col, ctype in (('correo_enviado_at', "TEXT DEFAULT ''"), ('correo_destinatario', "TEXT DEFAULT ''"),
                           ('correo_estado', "TEXT DEFAULT 'no_enviado'")):
            if col not in cols_edoc:
                conn.execute(f"ALTER TABLE electronic_documents ADD COLUMN {col} {ctype}")

        # Migración incremental: asiento_id en accounts_receivable y pos_movements (Fase 7)
        cols_ar = {r['name'] for r in conn.execute("PRAGMA table_info(accounts_receivable)").fetchall()}
        if 'asiento_id' not in cols_ar:
            conn.execute("ALTER TABLE accounts_receivable ADD COLUMN asiento_id TEXT DEFAULT ''")

        cols_pos_mov = {r['name'] for r in conn.execute("PRAGMA table_info(pos_movements)").fetchall()}
        if 'asiento_id' not in cols_pos_mov:
            conn.execute("ALTER TABLE pos_movements ADD COLUMN asiento_id TEXT DEFAULT ''")
        for codigo, desc, pct in DEFAULT_CR_TAX_RATES:
            conn.execute(
                "INSERT OR IGNORE INTO tax_rates (codigo, descripcion, porcentaje, activo) VALUES (?, ?, ?, 1)",
                (codigo, desc, pct)
            )


def get_or_init_company_context(company_id: str, current_user: str = '', db_path=None):
    """
    Obtiene el contexto comercial de una empresa registrada en `clientes`.
    Si aún no tiene configuración en `company_fe_settings`, sucursal `001` o terminal `00001`,
    los inicializa automáticamente reutilizando los datos del perfil existente sin duplicar ni pedirlos de nuevo.
    Nunca expone contraseñas ni certificados cifrados.
    """
    cid = str(company_id or '').strip()
    if not cid:
        raise ValueError("company_id es requerido")

    with database.get_db(db_path) as conn:
        cli = conn.execute("SELECT * FROM clientes WHERE id = ?", (cid,)).fetchone()
        if not cli:
            raise LookupError(f"Empresa {cid} no encontrada")

        cli_dict = dict(cli)
        tipo_id_map = {'PF': '01', 'PJ': '02', 'DIMEX': '03', 'NITE': '04'}
        tipo_id = tipo_id_map.get(cli_dict.get('tipo', 'PF'), '01')

        # 1. Asegurar configuración en company_fe_settings
        st = conn.execute("SELECT * FROM company_fe_settings WHERE company_id = ?", (cid,)).fetchone()
        if not st:
            conn.execute("""
                INSERT INTO company_fe_settings (
                    company_id, nombre_comercial, razon_social, identificacion,
                    tipo_identificacion, actividad_principal, otras_senas,
                    telefono, email, correo_envio_comprobantes, ambiente_hacienda
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'stag')
            """, (
                cid,
                cli_dict.get('nombre', ''),
                cli_dict.get('nombre', ''),
                cid,
                tipo_id,
                cli_dict.get('actividad', ''),
                cli_dict.get('direccion', ''),
                cli_dict.get('telefono', ''),
                cli_dict.get('email', ''),
                cli_dict.get('email', '')
            ))
            st = conn.execute("SELECT * FROM company_fe_settings WHERE company_id = ?", (cid,)).fetchone()

        # 2. Asegurar sucursal predeterminada 001
        branch = conn.execute(
            "SELECT * FROM company_branches WHERE company_id = ? ORDER BY codigo_sucursal ASC LIMIT 1",
            (cid,)
        ).fetchone()
        if not branch:
            branch_id = str(uuid.uuid4())
            conn.execute("""
                INSERT INTO company_branches (id, company_id, codigo_sucursal, nombre, direccion, telefono, activo)
                VALUES (?, ?, '001', 'Casa Matriz', ?, ?, 1)
            """, (branch_id, cid, cli_dict.get('direccion', ''), cli_dict.get('telefono', '')))
            branch = conn.execute("SELECT * FROM company_branches WHERE id = ?", (branch_id,)).fetchone()

        # 3. Asegurar terminal predeterminada 00001
        terminals = conn.execute(
            "SELECT * FROM company_terminals WHERE company_id = ? ORDER BY codigo_sucursal, codigo_terminal",
            (cid,)
        ).fetchall()
        if not terminals:
            term_id = str(uuid.uuid4())
            conn.execute("""
                INSERT INTO company_terminals (id, company_id, branch_id, codigo_sucursal, codigo_terminal, activo)
                VALUES (?, ?, ?, '001', '00001', 1)
            """, (term_id, cid, branch['id']))
            terminals = conn.execute(
                "SELECT * FROM company_terminals WHERE company_id = ? ORDER BY codigo_sucursal, codigo_terminal",
                (cid,)
            ).fetchall()

        st_dict = dict(st)
        # Eliminar cualquier campo sensible antes de retornar al frontend
        has_hacienda_creds = bool(st_dict.get('hacienda_username') and st_dict.get('hacienda_password_enc'))
        st_dict['has_hacienda_password'] = bool(st_dict.get('hacienda_password_enc'))
        st_dict['has_cert_pin'] = bool(st_dict.get('cert_pin_enc'))
        st_dict['has_smtp_password'] = bool(st_dict.get('smtp_password_enc'))
        for secret_key in ('hacienda_password_enc', 'cert_pin_enc', 'cert_file_enc', 'smtp_password_enc'):
            st_dict.pop(secret_key, None)
        u = st_dict.get('hacienda_username') or ''
        st_dict['hacienda_username'] = (u[:2] + '***' + u[-2:]) if len(u) > 4 else ('***' if u else '')
        st_dict['has_hacienda_credentials'] = has_hacienda_creds
        st_dict['has_cert_file'] = bool(st_dict.get('has_cert_file'))

        tax_rates = [dict(r) for r in conn.execute("SELECT * FROM tax_rates WHERE activo = 1 ORDER BY codigo").fetchall()]

        return {
            'company_id': cid,
            'usuario_autenticado': current_user,
            'profile': {
                'id': cli_dict['id'],
                'nombre': cli_dict['nombre'],
                'tipo': cli_dict['tipo'],
                'telefono': cli_dict.get('telefono', ''),
                'email': cli_dict.get('email', ''),
                'direccion': cli_dict.get('direccion', ''),
                'actividad': cli_dict.get('actividad', ''),
                'representante': cli_dict.get('representante', ''),
                'cuenta_id': cli_dict.get('cuenta_id', '')
            },
            'settings': st_dict,
            'branches': [dict(branch)],
            'terminals': [dict(t) for t in terminals],
            'tax_rates': tax_rates
        }


def get_dashboard_stats(
    company_id: str,
    fecha_inicio: str = '',
    fecha_fin: str = '',
    mes: str = '',
    anio: str = '',
    customer_id: str = '',
    estado: str = '',
    db_path=None
):
    """
    Calcula en tiempo real los 10 indicadores principales y los 7 gráficos estadísticos
    del Dashboard del Sistema de Ventas a partir de datos reales en SQLite,
    estrictamente aislados por `company_id`.
    """
    cid = str(company_id or '').strip()
    if not cid:
        raise ValueError("company_id es requerido")

    today_str = date.today().isoformat()
    cur_year = str(anio or date.today().year).strip()
    cur_month = str(mes or '').strip().zfill(2) if mes else ''
    default_ym = f"{cur_year}-{cur_month}" if cur_month else today_str[:7]

    with database.get_db(db_path) as conn:
        # ── 1. Construcción de filtros dinámicos para consultas analíticas ──
        where_clauses = ["ed.company_id = ?"]
        params = [cid]

        if fecha_inicio:
            where_clauses.append("substr(ed.fecha_emision, 1, 10) >= ?")
            params.append(fecha_inicio[:10])
        if fecha_fin:
            where_clauses.append("substr(ed.fecha_emision, 1, 10) <= ?")
            params.append(fecha_fin[:10])
        if not fecha_inicio and not fecha_fin:
            if cur_month:
                where_clauses.append("substr(ed.fecha_emision, 1, 7) = ?")
                params.append(f"{cur_year}-{cur_month}")
            elif cur_year:
                where_clauses.append("substr(ed.fecha_emision, 1, 4) = ?")
                params.append(cur_year)

        if customer_id:
            where_clauses.append("ed.customer_id = ?")
            params.append(customer_id)

        if estado:
            where_clauses.append("ed.estado_comprobante = ?")
            params.append(estado)

        where_sql = " AND ".join(where_clauses)

        # Documentos de venta efectivos (Factura 01 y Tiquete 04 que no estén en borrador ni rechazados)
        valid_sale_filter = "ed.tipo_documento IN ('01','04') AND ed.estado_comprobante NOT IN ('borrador','rechazado')"

        # ── 2. Los 10 Indicadores Principales (KPIs) ──
        # 1) Ventas del día (fecha de hoy o fecha_fin si se filtra un día exacto)
        target_day = fecha_fin[:10] if (fecha_inicio and fecha_fin and fecha_inicio == fecha_fin) else today_str
        ventas_dia = conn.execute(f"""
            SELECT COALESCE(SUM(ed.total_comprobante), 0)
            FROM electronic_documents ed
            WHERE ed.company_id = ? AND substr(ed.fecha_emision, 1, 10) = ? AND {valid_sale_filter}
        """, (cid, target_day)).fetchone()[0]

        # 2) Ventas del mes (mes filtrado o mes actual)
        ventas_mes = conn.execute(f"""
            SELECT COALESCE(SUM(ed.total_comprobante), 0)
            FROM electronic_documents ed
            WHERE ed.company_id = ? AND substr(ed.fecha_emision, 1, 7) = ? AND {valid_sale_filter}
        """, (cid, default_ym)).fetchone()[0]

        # 3) Total de facturas emitidas (tipo 01 bajo filtro activo)
        total_facturas = conn.execute(f"""
            SELECT COUNT(*) FROM electronic_documents ed
            WHERE {where_sql} AND ed.tipo_documento = '01' AND ed.estado_comprobante != 'borrador'
        """, params).fetchone()[0]

        # 4) Total de tiquetes emitidos (tipo 04 bajo filtro activo)
        total_tiquetes = conn.execute(f"""
            SELECT COUNT(*) FROM electronic_documents ed
            WHERE {where_sql} AND ed.tipo_documento = '04' AND ed.estado_comprobante != 'borrador'
        """, params).fetchone()[0]

        # 5) Facturas pendientes de respuesta de Hacienda
        pendientes_hacienda = conn.execute("""
            SELECT COUNT(*) FROM electronic_documents ed
            WHERE ed.company_id = ?
              AND ed.estado_comprobante IN ('pendiente_validacion','generado','firmado','pendiente_envio','enviado','recibido_hacienda','en_proceso')
        """, (cid,)).fetchone()[0]

        # 6) Facturas rechazadas (o con error de comunicación)
        rechazadas_hacienda = conn.execute("""
            SELECT COUNT(*) FROM electronic_documents ed
            WHERE ed.company_id = ?
              AND ed.estado_comprobante IN ('rechazado','error_comunicacion')
        """, (cid,)).fetchone()[0]

        # 7) Cuentas por cobrar (saldo pendiente total de la empresa)
        cuentas_por_cobrar = conn.execute("""
            SELECT COALESCE(SUM(saldo_pendiente), 0)
            FROM accounts_receivable
            WHERE company_id = ? AND estado IN ('pendiente','parcial','vencida')
        """, (cid,)).fetchone()[0]

        # 8) Clientes registrados (activos en el directorio comercial de la empresa)
        clientes_registrados = conn.execute("""
            SELECT COUNT(*) FROM sales_customers
            WHERE company_id = ? AND estado = 'activo'
        """, (cid,)).fetchone()[0]

        # 9) Productos registrados (activos en el catálogo de la empresa)
        productos_registrados = conn.execute("""
            SELECT COUNT(*) FROM products
            WHERE company_id = ? AND estado = 'activo'
        """, (cid,)).fetchone()[0]

        # 10) Valor del inventario (existencia * precio_compra o precio_venta si compra es 0)
        valor_inventario = conn.execute("""
            SELECT COALESCE(SUM(
                CASE
                    WHEN existencia > 0 THEN existencia * (CASE WHEN precio_compra > 0 THEN precio_compra ELSE precio_venta END)
                    ELSE 0
                END
            ), 0)
            FROM products
            WHERE company_id = ? AND estado = 'activo' AND controla_inventario = 1
        """, (cid,)).fetchone()[0]

        # ── 3. Gráficos Estadísticos ──
        # A) Ventas por mes y comparación entre períodos (Año seleccionado vs Año anterior)
        prev_year = str(int(cur_year) - 1) if cur_year.isdigit() else '2025'
        month_labels = ['Ene', 'Feb', 'Mar', 'Abr', 'May', 'Jun', 'Jul', 'Ago', 'Sep', 'Oct', 'Nov', 'Dic']
        actual_by_m = {str(i).zfill(2): 0.0 for i in range(1, 13)}
        prev_by_m = {str(i).zfill(2): 0.0 for i in range(1, 13)}

        rows_cur_yr = conn.execute(f"""
            SELECT substr(ed.fecha_emision, 6, 2) AS m, COALESCE(SUM(ed.total_comprobante), 0) AS tot
            FROM electronic_documents ed
            WHERE ed.company_id = ? AND substr(ed.fecha_emision, 1, 4) = ? AND {valid_sale_filter}
            GROUP BY m
        """, (cid, cur_year)).fetchall()
        for r in rows_cur_yr:
            if r['m'] in actual_by_m:
                actual_by_m[r['m']] = round(float(r['tot'] or 0), 2)

        rows_prev_yr = conn.execute(f"""
            SELECT substr(ed.fecha_emision, 6, 2) AS m, COALESCE(SUM(ed.total_comprobante), 0) AS tot
            FROM electronic_documents ed
            WHERE ed.company_id = ? AND substr(ed.fecha_emision, 1, 4) = ? AND {valid_sale_filter}
            GROUP BY m
        """, (cid, prev_year)).fetchall()
        for r in rows_prev_yr:
            if r['m'] in prev_by_m:
                prev_by_m[r['m']] = round(float(r['tot'] or 0), 2)

        ventas_por_mes = {
            'labels': month_labels,
            'anio_actual': cur_year,
            'anio_previo': prev_year,
            'actual': [actual_by_m[str(i).zfill(2)] for i in range(1, 13)],
            'previo': [prev_by_m[str(i).zfill(2)] for i in range(1, 13)],
        }

        # B) Ventas por día (bajo el filtro activo)
        rows_dia = conn.execute(f"""
            SELECT substr(ed.fecha_emision, 1, 10) AS fecha, COALESCE(SUM(ed.total_comprobante), 0) AS tot
            FROM electronic_documents ed
            WHERE {where_sql} AND {valid_sale_filter}
            GROUP BY fecha
            ORDER BY fecha ASC
            LIMIT 31
        """, params).fetchall()
        ventas_por_dia = [{'fecha': r['fecha'], 'total': round(float(r['tot'] or 0), 2)} for r in rows_dia]

        # C) Productos más vendidos
        rows_prod = conn.execute(f"""
            SELECT l.descripcion AS nombre,
                   COALESCE(SUM(l.cantidad), 0) AS cantidad,
                   COALESCE(SUM(l.total_linea), 0) AS monto
            FROM electronic_document_lines l
            JOIN electronic_documents ed ON ed.id = l.document_id
            WHERE {where_sql} AND {valid_sale_filter}
            GROUP BY l.descripcion
            ORDER BY monto DESC
            LIMIT 8
        """, params).fetchall()
        productos_mas_vendidos = [
            {'nombre': r['nombre'], 'cantidad': round(float(r['cantidad'] or 0), 2), 'monto': round(float(r['monto'] or 0), 2)}
            for r in rows_prod
        ]

        # D) Clientes con mayor volumen de compras
        rows_cli = conn.execute(f"""
            SELECT COALESCE(NULLIF(TRIM(ed.customer_nombre), ''), 'Consumidor Final') AS nombre,
                   COUNT(*) AS documentos,
                   COALESCE(SUM(ed.total_comprobante), 0) AS monto
            FROM electronic_documents ed
            WHERE {where_sql} AND {valid_sale_filter}
            GROUP BY nombre
            ORDER BY monto DESC
            LIMIT 8
        """, params).fetchall()
        clientes_mayor_volumen = [
            {'nombre': r['nombre'], 'documentos': int(r['documentos']), 'monto': round(float(r['monto'] or 0), 2)}
            for r in rows_cli
        ]

        # E) Distribución de ventas por categoría
        rows_cat = conn.execute(f"""
            SELECT COALESCE(NULLIF(TRIM(l.categoria_nombre), ''), 'General') AS categoria,
                   COALESCE(SUM(l.total_linea), 0) AS monto
            FROM electronic_document_lines l
            JOIN electronic_documents ed ON ed.id = l.document_id
            WHERE {where_sql} AND {valid_sale_filter}
            GROUP BY categoria
            ORDER BY monto DESC
        """, params).fetchall()
        ventas_por_categoria = [
            {'categoria': r['categoria'], 'monto': round(float(r['monto'] or 0), 2)}
            for r in rows_cat
        ]

        # F) Ventas según medio de pago
        rows_mp = conn.execute(f"""
            SELECT ed.medio_pago AS codigo, COALESCE(SUM(ed.total_comprobante), 0) AS monto
            FROM electronic_documents ed
            WHERE {where_sql} AND {valid_sale_filter}
            GROUP BY ed.medio_pago
            ORDER BY monto DESC
        """, params).fetchall()
        ventas_por_medio_pago = [
            {
                'codigo': r['codigo'],
                'medio': MEDIO_PAGO_LABELS.get(r['codigo'], f"Medio {r['codigo']}"),
                'monto': round(float(r['monto'] or 0), 2)
            }
            for r in rows_mp
        ]

        # G) Ventas de contado frente a ventas a crédito
        rows_cond = conn.execute(f"""
            SELECT CASE WHEN ed.condicion_venta = '02' THEN 'Crédito' ELSE 'Contado' END AS condicion,
                   COALESCE(SUM(ed.total_comprobante), 0) AS monto
            FROM electronic_documents ed
            WHERE {where_sql} AND {valid_sale_filter}
            GROUP BY condicion
        """, params).fetchall()
        contado_vs_credito = {'Contado': 0.0, 'Crédito': 0.0}
        for r in rows_cond:
            contado_vs_credito[r['condicion']] = round(float(r['monto'] or 0), 2)

        # ── 4. Últimos comprobantes emitidos y lista de clientes para el filtro ──
        ultimos = conn.execute(f"""
            SELECT ed.id, ed.fecha_emision, ed.tipo_documento, ed.consecutivo,
                   ed.customer_nombre, ed.condicion_venta, ed.total_comprobante, ed.estado_comprobante
            FROM electronic_documents ed
            WHERE {where_sql}
            ORDER BY ed.fecha_emision DESC, ed.created_at DESC
            LIMIT 10
        """, params).fetchall()

        customers_list = conn.execute("""
            SELECT id, nombre, identificacion
            FROM sales_customers
            WHERE company_id = ? AND estado = 'activo'
            ORDER BY nombre ASC
        """, (cid,)).fetchall()

        return {
            'company_id': cid,
            'kpis': {
                'ventas_dia': round(float(ventas_dia or 0), 2),
                'ventas_mes': round(float(ventas_mes or 0), 2),
                'total_facturas_emitidas': int(total_facturas or 0),
                'total_tiquetes_emitidos': int(total_tiquetes or 0),
                'facturas_pendientes_hacienda': int(pendientes_hacienda or 0),
                'facturas_rechazadas': int(rechazadas_hacienda or 0),
                'cuentas_por_cobrar': round(float(cuentas_por_cobrar or 0), 2),
                'clientes_registrados': int(clientes_registrados or 0),
                'productos_registrados': int(productos_registrados or 0),
                'valor_inventario': round(float(valor_inventario or 0), 2),
            },
            'charts': {
                'ventas_por_mes': ventas_por_mes,
                'ventas_por_dia': ventas_por_dia,
                'productos_mas_vendidos': productos_mas_vendidos,
                'clientes_mayor_volumen': clientes_mayor_volumen,
                'ventas_por_categoria': ventas_por_categoria,
                'ventas_por_medio_pago': ventas_por_medio_pago,
                'contado_vs_credito': contado_vs_credito,
            },
            'ultimos_comprobantes': [dict(r) for r in ultimos],
            'customers_filter_list': [dict(c) for c in customers_list],
        }


# Inicializar tablas comerciales al importar el módulo
init_ventas_schema()
