#!/usr/bin/env python3
"""
FACTURAMI - CLI Runner para el Poller de Validación de Hacienda.
================================================================
Uso:
    python scripts/poll_hacienda.py
    python scripts/poll_hacienda.py --company-id 3101888999
    python scripts/poll_hacienda.py --limit 100
"""
import argparse
import os
import sys

# Asegurar que el directorio raíz del proyecto esté en el path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.fe_poller_service import poll_pending_hacienda_documents

def main():
    parser = argparse.ArgumentParser(description="FACTURAMI - Poller de Hacienda CR")
    parser.add_argument('--company-id', type=str, default=None, help="Cédula/ID de la empresa (opcional, por defecto todas)")
    parser.add_argument('--limit', type=int, default=50, help="Límite de documentos a consultar (default: 50)")
    args = parser.parse_args()

    print(f"=== [FACTURAMI] Consultando comprobantes pendientes ante Hacienda ===")
    res = poll_pending_hacienda_documents(company_id=args.company_id, limit=args.limit)
    print(f">> Consultados: {res['consultados']}")
    print(f">> Aceptados:   {res['aceptados']}")
    print(f">> Rechazados:  {res['rechazados']}")
    print(f">> Pendientes:  {res['pendientes']}")
    print(f">> Errores:     {res['errores']}")

    if res['detalles']:
        print("\n--- Detalle de Comprobantes ---")
        for d in res['detalles']:
            print(f"  • [{d.get('consecutivo')}] Estado: {d.get('estado')} | Mensaje: {d.get('mensaje')}")
    print("=== Fin de ejecución ===")

if __name__ == '__main__':
    main()
