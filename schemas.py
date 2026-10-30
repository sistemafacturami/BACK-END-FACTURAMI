"""
FACTURAMI - Schemas de validacion para cada endpoint.
"""

# ══════════════════════════════════════════════════════════════════════════
#  Auth
# ══════════════════════════════════════════════════════════════════════════

LOGIN_SCHEMA = {
    'username': {'required': True, 'type': str, 'max_len': 50},
    'password': {'required': True, 'type': str, 'max_len': 128},
}

# ══════════════════════════════════════════════════════════════════════════
#  Facturas Queue (POST desde n8n)
# ══════════════════════════════════════════════════════════════════════════

QUEUE_ADD_SCHEMA = {
    'FACTURA':    {'required': False, 'type': str, 'max_len': 100, 'default': ''},
    'PROVEEDOR':  {'required': False, 'type': str, 'max_len': 200, 'default': ''},
    'TOTAL':      {'required': False, 'type': str, 'max_len': 50, 'default': '0,00'},
    'FECHA':      {'required': False, 'type': str, 'max_len': 20, 'default': ''},
    'Categoria':  {'required': False, 'type': str, 'max_len': 100, 'default': ''},
    'SUB TOTAL':  {'required': False, 'type': str, 'max_len': 50, 'default': '0,00'},
    'IVA 1%':     {'required': False, 'type': str, 'max_len': 50, 'default': '0,00'},
    'IVA 2%':     {'required': False, 'type': str, 'max_len': 50, 'default': '0,00'},
    'IVA 4%':     {'required': False, 'type': str, 'max_len': 50, 'default': '0,00'},
    'IVA 13%':    {'required': False, 'type': str, 'max_len': 50, 'default': '0,00'},
    'Notas':      {'required': False, 'type': str, 'max_len': 500, 'default': ''},
}

# ══════════════════════════════════════════════════════════════════════════
#  Clientes
# ══════════════════════════════════════════════════════════════════════════

CLIENTE_SCHEMA = {
    'id':   {'required': True,  'type': str, 'max_len': 20},
    'type': {'required': True,  'type': str, 'choices': ('PF', 'PJ')},
    'name': {'required': True,  'type': str, 'max_len': 100},
    'tel':  {'required': False, 'type': str, 'max_len': 20, 'default': ''},
    'mail': {'required': False, 'type': str, 'max_len': 100, 'default': ''},
    'dir':  {'required': False, 'type': str, 'max_len': 200, 'default': ''},
    'act':  {'required': False, 'type': str, 'max_len': 100, 'default': ''},
    'rep':  {'required': False, 'type': str, 'max_len': 100, 'default': ''},
}

# ══════════════════════════════════════════════════════════════════════════
#  Transacciones
# ══════════════════════════════════════════════════════════════════════════

TRANSACCION_SCHEMA = {
    'modulo':  {'required': True, 'type': str, 'choices': ('ingresos', 'compras', 'gastos', 'costos', 'obras')},
    'fecha':   {'required': True, 'type': str, 'max_len': 10},
    'doc':     {'required': False, 'type': str, 'max_len': 50, 'default': ''},
    'tipo':    {'required': False, 'type': str, 'choices': ('Contado', 'Credito', 'Nota de Credito'), 'default': 'Contado'},
    'l':       {'required': True, 'type': str, 'max_len': 200},
    'prov':    {'required': False, 'type': str, 'max_len': 200, 'default': ''},
    'tf':      {'required': False, 'type': (int, float), 'min': 0, 'default': 0},
    'ns':      {'required': False, 'type': (int, float), 'min': 0, 'default': 0},
    'ex':      {'required': False, 'type': (int, float), 'min': 0, 'default': 0},
    'c05':     {'required': False, 'type': (int, float), 'min': 0, 'default': 0},
    'c1':      {'required': False, 'type': (int, float), 'min': 0, 'default': 0},
    'c2':      {'required': False, 'type': (int, float), 'min': 0, 'default': 0},
    'c4':      {'required': False, 'type': (int, float), 'min': 0, 'default': 0},
    'c8':      {'required': False, 'type': (int, float), 'min': 0, 'default': 0},
    'c13':     {'required': False, 'type': (int, float), 'min': 0, 'default': 0},
}
