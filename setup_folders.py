import os

dirs = [
    'tmp',
    'logs',
    'cache/npm',
    'cache/pip',
    'cache/browser',
    'storage',
    'backups',
    'docs/n8n'
]

for d in dirs:
    os.makedirs(d, exist_ok=True)
    print(f"Directorio listo: {d}")
