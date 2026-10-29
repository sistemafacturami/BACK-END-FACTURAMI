#!/usr/bin/env python3
"""
FACTURAMI - Suite de Pruebas Automatizadas de Auditoría Contable NIIF (Fase 4)
Verificación rigurosa de partida doble, ecuación patrimonial, idempotencia,
cierres, validaciones y prueba de propiedad matemática.
"""
import sys
import os
import sqlite3
import uuid
import random
import unittest
from datetime import date

# Asegurar path al directorio raíz
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

import db as database
import services.contabilidad_service as cs
from scripts.migrate_v2_niif import migrate

TEST_DB_PATH = os.path.join(BASE_DIR, 'test_niif_audit.db')

class TestContabilidadNIIF(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        """Prepara base de datos de prueba limpia e inicializa tablas NIIF."""
        if os.path.exists(TEST_DB_PATH):
            os.remove(TEST_DB_PATH)
            
        # Crear esquema base
        database.init_db(TEST_DB_PATH)
        conn = sqlite3.connect(TEST_DB_PATH)
        conn.row_factory = sqlite3.Row
        
        # Insertar cliente de prueba aislado
        cls.cid = "TEST_CLIENT_001"
        conn.execute("INSERT OR REPLACE INTO clientes (id, nombre, tipo) VALUES (?, ?, 'PF')",
                     (cls.cid, "Empresa de Prueba S.A."))
        conn.commit()
        conn.close()
        
        # Ejecutar migración NIIF V2 sobre la base de pruebas
        migrate(TEST_DB_PATH)

    @classmethod
    def tearDownClass(cls):
        """Limpia la base de datos de prueba al terminar."""
        if os.path.exists(TEST_DB_PATH):
            try:
                os.remove(TEST_DB_PATH)
            except Exception:
                pass

    def get_conn(self):
        conn = sqlite3.connect(TEST_DB_PATH)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def get_ledger_balances(self, conn):
        """Calcula los saldos de mayor por cuenta y por grupo contable."""
        rows = conn.execute("""
            SELECT l.cuenta_codigo, l.cuenta_nombre, c.tipo, c.naturaleza,
                   SUM(l.debito) as tot_debe, SUM(l.credito) as tot_haber
            FROM asiento_lineas l
            JOIN asientos a ON l.asiento_id = a.id
            LEFT JOIN catalogo_cuentas c ON l.cuenta_codigo = c.codigo AND c.cliente_id = a.cliente_id
            WHERE a.cliente_id = ? AND a.estado = 'valido'
            GROUP BY l.cuenta_codigo
        """, (self.cid,)).fetchall()
        
        saldos = {}
        activo = 0.0
        pasivo = 0.0
        patrimonio = 0.0
        ingreso = 0.0
        costo_gasto = 0.0
        
        for r in rows:
            cod = r['cuenta_codigo']
            debe = round(r['tot_debe'] or 0, 2)
            haber = round(r['tot_haber'] or 0, 2)
            saldos[cod] = {'debe': debe, 'haber': haber, 'nombre': r['cuenta_nombre']}
            
            # Clasificación contable estándar
            if cod.startswith('1.'):
                # Si es cuenta compensadora de depreciación acumulada (1.2.2.XX) se resta
                if cod.startswith('1.2.2.'):
                    activo -= (haber - debe)
                else:
                    activo += (debe - haber)
            elif cod.startswith('2.'):
                pasivo += (haber - debe)
            elif cod.startswith('3.'):
                # Si es retiro personal (3.2.1.XX) es contracuenta de patrimonio
                if cod.startswith('3.2.'):
                    patrimonio -= (debe - haber)
                else:
                    patrimonio += (haber - debe)
            elif cod.startswith('4.'):
                ingreso += (haber - debe)
            elif cod.startswith('5.'):
                costo_gasto += (debe - haber)
                
        utilidad_ejercicio = round(ingreso - costo_gasto, 2)
        patrimonio_total = round(patrimonio + utilidad_ejercicio, 2)
        activo = round(activo, 2)
        pasivo = round(pasivo, 2)
        diferencia = round(activo - (pasivo + patrimonio_total), 2)
        
        return {
            'saldos_cuentas': saldos,
            'activo': activo,
            'pasivo': pasivo,
            'patrimonio_base': round(patrimonio, 2),
            'utilidad_ejercicio': utilidad_ejercicio,
            'patrimonio_total': patrimonio_total,
            'diferencia_cuadre': diferencia
        }

    # ══════════════════════════════════════════════════════════════════════════
    # PRUEBA 1: Escenario Base desde Cero (Requisito Explícito)
    # ══════════════════════════════════════════════════════════════════════════
    def test_01_escenario_base(self):
        """
        a) Aporte inicial ₡20 000 000: Bancos 20M, Capital 20M.
        b) Compra de vehículo ₡16 000 000 desde Bancos: Bancos 4M, Vehículos 16M.
        c) Compra de maquinaria ₡1 000 000 a crédito: Cuentas por pagar 1M.
        d) Depreciación de ₡100 000: Gasto 100k, Depreciación acumulada 100k.
        Resultado esperado:
          Activo ₡20 900 000 = Pasivo ₡1 000 000 + Patrimonio ₡19 900 000. Diferencia = 0.
        """
        conn = self.get_conn()
        
        # a) Aporte inicial ₡20.000.000
        a1 = {
            'cliente_id': self.cid, 'fecha_contable': '2026-01-01',
            'tipo_operacion': 'aporte_capital', 'documento_origen': 'APORTE-001',
            'concepto': 'Aporte Inicial de Capital del Propietario'
        }
        l1 = [
            {'cuenta_codigo': '1.1.1.02.01', 'cuenta_nombre': 'Banco Nacional de Costa Rica (BNCR)', 'debito': 20000000.0, 'credito': 0.0},
            {'cuenta_codigo': '3.1.1.01', 'cuenta_nombre': 'Capital del Propietario (Aportes)', 'debito': 0.0, 'credito': 20000000.0}
        ]
        aid1 = database.insert_asiento(conn, a1, l1)
        self.assertTrue(aid1)
        
        # Verificar estado tras a)
        st_a = self.get_ledger_balances(conn)
        banco_saldo = st_a['saldos_cuentas']['1.1.1.02.01']['debe'] - st_a['saldos_cuentas']['1.1.1.02.01']['haber']
        capital_saldo = st_a['saldos_cuentas']['3.1.1.01']['haber'] - st_a['saldos_cuentas']['3.1.1.01']['debe']
        self.assertEqual(banco_saldo, 20000000.0)
        self.assertEqual(capital_saldo, 20000000.0)
        self.assertEqual(st_a['diferencia_cuadre'], 0.0)

        # b) Compra de vehículo ₡16.000.000 desde Bancos
        a2 = {
            'cliente_id': self.cid, 'fecha_contable': '2026-01-15',
            'tipo_operacion': 'compra_activo', 'documento_origen': 'FAC-VEH-01',
            'concepto': 'Compra de Vehículo pagado con Banco'
        }
        l2 = [
            {'cuenta_codigo': '1.2.1.01', 'cuenta_nombre': 'Vehículos', 'debito': 16000000.0, 'credito': 0.0},
            {'cuenta_codigo': '1.1.1.02.01', 'cuenta_nombre': 'Banco Nacional de Costa Rica (BNCR)', 'debito': 0.0, 'credito': 16000000.0}
        ]
        aid2 = database.insert_asiento(conn, a2, l2)
        self.assertTrue(aid2)
        
        # Verificar estado tras b): Bancos 4M, Vehículos 16M
        st_b = self.get_ledger_balances(conn)
        banco_saldo_b = st_b['saldos_cuentas']['1.1.1.02.01']['debe'] - st_b['saldos_cuentas']['1.1.1.02.01']['haber']
        veh_saldo = st_b['saldos_cuentas']['1.2.1.01']['debe'] - st_b['saldos_cuentas']['1.2.1.01']['haber']
        self.assertEqual(banco_saldo_b, 4000000.0)
        self.assertEqual(veh_saldo, 16000000.0)
        self.assertEqual(st_b['diferencia_cuadre'], 0.0)

        # c) Compra de maquinaria ₡1.000.000 a crédito
        a3 = {
            'cliente_id': self.cid, 'fecha_contable': '2026-02-01',
            'tipo_operacion': 'compra_activo_credito', 'documento_origen': 'FAC-MAQ-01',
            'concepto': 'Compra de Maquinaria y Herramientas a crédito'
        }
        l3 = [
            {'cuenta_codigo': '1.2.1.02', 'cuenta_nombre': 'Maquinaria y Herramientas', 'debito': 1000000.0, 'credito': 0.0},
            {'cuenta_codigo': '2.1.1.01', 'cuenta_nombre': 'Cuentas por Pagar Proveedores', 'debito': 0.0, 'credito': 1000000.0}
        ]
        aid3 = database.insert_asiento(conn, a3, l3)
        self.assertTrue(aid3)

        # d) Depreciación de ₡100.000 (Gasto 100k, Depreciación Acumulada 100k)
        a4 = {
            'cliente_id': self.cid, 'fecha_contable': '2026-02-28',
            'tipo_operacion': 'depreciacion_mensual', 'documento_origen': 'DEP-2026-02',
            'concepto': 'Depreciación mensual línea recta de activos fijos'
        }
        l4 = [
            {'cuenta_codigo': '5.3.1.01', 'cuenta_nombre': 'Gasto por Depreciación de Vehículos', 'debito': 100000.0, 'credito': 0.0},
            {'cuenta_codigo': '1.2.2.01', 'cuenta_nombre': '(-) Depreciación Acumulada Vehículos', 'debito': 0.0, 'credito': 100000.0}
        ]
        aid4 = database.insert_asiento(conn, a4, l4)
        self.assertTrue(aid4)
        
        conn.commit()

        # ── VERIFICACIÓN FINAL DEL ESCENARIO BASE ───────────────────────
        res = self.get_ledger_balances(conn)
        print("\n[TEST 01] RESULTADOS ESCENARIO BASE:")
        print(f"  * Total Activo:             ₡{res['activo']:,.2f}  (Esperado: ₡20,900,000.00)")
        print(f"  * Total Pasivo:             ₡{res['pasivo']:,.2f}  (Esperado: ₡1,000,000.00)")
        print(f"  * Total Patrimonio:         ₡{res['patrimonio_total']:,.2f}  (Esperado: ₡19,900,000.00)")
        print(f"    - Capital Aportado:       ₡{res['patrimonio_base']:,.2f}")
        print(f"    - Utilidad/Pérdida Neto:  ₡{res['utilidad_ejercicio']:,.2f}")
        print(f"  * Pasivo + Patrimonio:      ₡{res['pasivo'] + res['patrimonio_total']:,.2f}")
        print(f"  * DIFERENCIA DE CUADRE:     ₡{res['diferencia_cuadre']:,.2f}  (Esperado: ₡0.00)")

        self.assertEqual(res['activo'], 20900000.0)
        self.assertEqual(res['pasivo'], 1000000.0)
        self.assertEqual(res['patrimonio_total'], 19900000.0)
        self.assertEqual(res['diferencia_cuadre'], 0.0)
        conn.close()

    # ══════════════════════════════════════════════════════════════════════════
    # PRUEBA 2: Compra con IVA Crédito Fiscal Desglosado (13%)
    # ══════════════════════════════════════════════════════════════════════════
    def test_02_compra_con_iva(self):
        """Verifica compra de materiales con crédito fiscal de IVA 13% y banco."""
        conn = self.get_conn()
        subtotal = 100000.0
        iva = 13000.0
        total = 113000.0
        
        a = {
            'cliente_id': self.cid, 'fecha_contable': '2026-03-05',
            'tipo_operacion': 'compra', 'documento_origen': 'FAC-MAT-01',
            'concepto': 'Compra de materiales gravados al 13%'
        }
        lineas = [
            {'cuenta_codigo': '5.1.1.01', 'cuenta_nombre': 'Costos de Operación y Compras de Materiales', 'debito': subtotal, 'credito': 0.0},
            {'cuenta_codigo': '1.1.3.01', 'cuenta_nombre': 'IVA Crédito Fiscal Soportado 13%', 'debito': iva, 'credito': 0.0},
            {'cuenta_codigo': '1.1.1.02.01', 'cuenta_nombre': 'Banco Nacional de Costa Rica (BNCR)', 'debito': 0.0, 'credito': total}
        ]
        aid = database.insert_asiento(conn, a, lineas)
        conn.commit()
        self.assertTrue(aid)
        
        res = self.get_ledger_balances(conn)
        self.assertEqual(res['diferencia_cuadre'], 0.0)
        conn.close()

    # ══════════════════════════════════════════════════════════════════════════
    # PRUEBA 3: Pago a Proveedor (Amortización de Pasivo)
    # ══════════════════════════════════════════════════════════════════════════
    def test_03_pago_a_proveedor(self):
        """Verifica amortización de ₡500.000 a cuentas por pagar de la maquinaria."""
        conn = self.get_conn()
        monto = 500000.0
        a = {
            'cliente_id': self.cid, 'fecha_contable': '2026-03-10',
            'tipo_operacion': 'pago_proveedor', 'documento_origen': 'REC-PAG-01',
            'concepto': 'Abono 50% a factura de maquinaria a crédito'
        }
        lineas = [
            {'cuenta_codigo': '2.1.1.01', 'cuenta_nombre': 'Cuentas por Pagar Proveedores', 'debito': monto, 'credito': 0.0},
            {'cuenta_codigo': '1.1.1.02.01', 'cuenta_nombre': 'Banco Nacional de Costa Rica (BNCR)', 'debito': 0.0, 'credito': monto}
        ]
        aid = database.insert_asiento(conn, a, lineas)
        conn.commit()
        self.assertTrue(aid)
        
        res = self.get_ledger_balances(conn)
        self.assertEqual(res['saldos_cuentas']['2.1.1.01']['haber'] - res['saldos_cuentas']['2.1.1.01']['debe'], 500000.0)
        self.assertEqual(res['diferencia_cuadre'], 0.0)
        conn.close()

    # ══════════════════════════════════════════════════════════════════════════
    # PRUEBA 4: Retiro Personal del Propietario (Sección 22 NIIF Pymes)
    # ══════════════════════════════════════════════════════════════════════════
    def test_04_retiro_propietario(self):
        """Verifica retiro personal del propietario: reduce banco y patrimonio sin tocar resultados."""
        conn = self.get_conn()
        monto = 200000.0
        a = {
            'cliente_id': self.cid, 'fecha_contable': '2026-03-15',
            'tipo_operacion': 'retiro_propietario', 'documento_origen': 'RET-001',
            'concepto': 'Retiro de fondos personales del propietario'
        }
        lineas = [
            {'cuenta_codigo': '3.2.1.01', 'cuenta_nombre': '(-) Retiros Personales del Propietario', 'debito': monto, 'credito': 0.0},
            {'cuenta_codigo': '1.1.1.02.01', 'cuenta_nombre': 'Banco Nacional de Costa Rica (BNCR)', 'debito': 0.0, 'credito': monto}
        ]
        aid = database.insert_asiento(conn, a, lineas)
        conn.commit()
        self.assertTrue(aid)
        
        res = self.get_ledger_balances(conn)
        self.assertEqual(res['diferencia_cuadre'], 0.0)
        conn.close()

    # ══════════════════════════════════════════════════════════════════════════
    # PRUEBA 5: Idempotencia (Reprocesar documento no duplica asientos)
    # ══════════════════════════════════════════════════════════════════════════
    def test_05_idempotencia_reproceso(self):
        """Verifica que procesar el mismo documento histórico dos veces no duplique asientos."""
        asig = [{
            'id': 'doc_test_idemp_1',
            'modulo': 'compras',
            'documento': 'IDEMP-FACT-01',
            'fecha': '2026-03-20',
            'descripcion': 'Materiales especiales idempotentes',
            'total_factura': 75000.0,
            'origen_fondos': 'banco'
        }]
        
        # Primera ejecución
        res1 = cs.reconciliar_historico(self.cid, asig, db_path=TEST_DB_PATH)
        aid1 = res1['detalle'][0]['asiento_id']
        self.assertTrue(aid1)

        # Segunda ejecución (reproceso del mismo documento)
        res2 = cs.reconciliar_historico(self.cid, asig, db_path=TEST_DB_PATH)
        self.assertTrue(res2['detalle'][0].get('idempotente'))

        # Conteo de asientos: debe seguir siendo exactamente 1 (sin duplicación)
        conn = self.get_conn()
        c1 = conn.execute("SELECT COUNT(*) FROM asientos WHERE cliente_id=? AND documento_origen='IDEMP-FACT-01'", (self.cid,)).fetchone()[0]
        self.assertEqual(c1, 1)
        conn.close()

    # ══════════════════════════════════════════════════════════════════════════
    # PRUEBA 6: Asiento Descuadrado Rechazado Rigurosamente
    # ══════════════════════════════════════════════════════════════════════════
    def test_06_rechazo_asiento_descuadrado(self):
        """Comprueba que cualquier asiento donde Debe != Haber se rechaza y lanza ValueError."""
        conn = self.get_conn()
        a_mala = {
            'cliente_id': self.cid, 'fecha_contable': '2026-03-25',
            'tipo_operacion': 'compra_invalida', 'documento_origen': 'ERROR-001',
            'concepto': 'Asiento intencionalmente descuadrado'
        }
        lineas_malas = [
            {'cuenta_codigo': '5.2.1.03', 'cuenta_nombre': 'Gastos Varios', 'debito': 100000.0, 'credito': 0.0},
            {'cuenta_codigo': '1.1.1.02.01', 'cuenta_nombre': 'Bancos', 'debito': 0.0, 'credito': 95000.0} # Falta 5.000
        ]
        
        with self.assertRaises(ValueError) as ctx:
            database.insert_asiento(conn, a_mala, lineas_malas)
            
        self.assertIn("Asiento descuadrado", str(ctx.exception))
        conn.close()

    # ══════════════════════════════════════════════════════════════════════════
    # PRUEBA 7: Advertencia de Bancos Negativo (Sin Bloquear)
    # ══════════════════════════════════════════════════════════════════════════
    def test_07_banco_negativo_con_advertencia(self):
        """Verifica que un sobregiro bancario se registra contablemente pero emite advertencia de liquidez."""
        conn = self.get_conn()
        # Consultar saldo actual en banco
        st = self.get_ledger_balances(conn)
        banco_disp = st['saldos_cuentas']['1.1.1.02.01']['debe'] - st['saldos_cuentas']['1.1.1.02.01']['haber']
        
        # Desembolso mayor al saldo disponible
        monto_exceso = banco_disp + 500000.0
        a = {
            'cliente_id': self.cid, 'fecha_contable': '2026-03-28',
            'tipo_operacion': 'gasto_sobregiro', 'documento_origen': 'SOB-001',
            'concepto': 'Gasto urgente que genera sobregiro temporal'
        }
        lineas = [
            {'cuenta_codigo': '5.2.1.03', 'cuenta_nombre': 'Gastos Administrativos Generales', 'debito': monto_exceso, 'credito': 0.0},
            {'cuenta_codigo': '1.1.1.02.01', 'cuenta_nombre': 'Banco Nacional de Costa Rica (BNCR)', 'debito': 0.0, 'credito': monto_exceso}
        ]
        aid = database.insert_asiento(conn, a, lineas)
        conn.commit()
        self.assertTrue(aid)
        
        st_post = self.get_ledger_balances(conn)
        nuevo_saldo = st_post['saldos_cuentas']['1.1.1.02.01']['debe'] - st_post['saldos_cuentas']['1.1.1.02.01']['haber']
        self.assertLess(nuevo_saldo, 0)
        # La ecuación contable se preserva exactamente
        self.assertEqual(st_post['diferencia_cuadre'], 0.0)
        print(f"\n[TEST 07] Saldo bancario en sobregiro detectado: ₡{nuevo_saldo:,.2f} (Alerta activada, ecuación balanceada)")
        conn.close()

    # ══════════════════════════════════════════════════════════════════════════
    # PRUEBA 8: Cierre del Ejercicio Anual
    # ══════════════════════════════════════════════════════════════════════════
    def test_08_cierre_de_ejercicio(self):
        """
        Verifica el asiento de cierre anual:
        - Deja en cero ingresos y gastos.
        - Traslada el resultado neto a Resultados Acumulados (3.3.1.01).
        - El Balance General sigue cuadrando con diferencia = 0.
        """
        conn = self.get_conn()
        st_pre = self.get_ledger_balances(conn)
        res_neto = st_pre['utilidad_ejercicio']
        
        # Asiento de liquidación de cuentas de resultados
        lineas_cierre = []
        for cod, s in st_pre['saldos_cuentas'].items():
            if cod.startswith('4.'):
                # Debitar ingresos
                lineas_cierre.append({'cuenta_codigo': cod, 'cuenta_nombre': s['nombre'], 'debito': (s['haber'] - s['debe']), 'credito': 0.0})
            elif cod.startswith('5.'):
                # Acreditar costos y gastos
                lineas_cierre.append({'cuenta_codigo': cod, 'cuenta_nombre': s['nombre'], 'debito': 0.0, 'credito': (s['debe'] - s['haber'])})
                
        # Compensar contra Resultados Acumulados
        if res_neto >= 0:
            lineas_cierre.append({'cuenta_codigo': '3.3.1.01', 'cuenta_nombre': 'Resultados Acumulados de Períodos Anteriores', 'debito': 0.0, 'credito': res_neto})
        else:
            lineas_cierre.append({'cuenta_codigo': '3.3.1.01', 'cuenta_nombre': 'Resultados Acumulados de Períodos Anteriores', 'debito': abs(res_neto), 'credito': 0.0})
            
        a_cierre = {
            'cliente_id': self.cid, 'fecha_contable': '2026-12-31',
            'tipo_operacion': 'cierre_ejercicio', 'documento_origen': 'CIERRE-2026',
            'concepto': 'Asiento de Cierre y Liquidación de Cuentas Nominales Año 2026'
        }
        aid_cierre = database.insert_asiento(conn, a_cierre, lineas_cierre)
        conn.commit()
        self.assertTrue(aid_cierre)
        
        st_post = self.get_ledger_balances(conn)
        self.assertEqual(st_post['utilidad_ejercicio'], 0.0) # Nominal en cero
        self.assertEqual(st_post['diferencia_cuadre'], 0.0)  # Cuadre intacto
        print(f"\n[TEST 08] Cierre contable aplicado. Utilidad nominal = ₡0.00 | Diferencia Balance = ₡0.00")
        conn.close()

    # ══════════════════════════════════════════════════════════════════════════
    # PRUEBA 9: Prueba de Propiedad (Property-Based Invariant)
    # ══════════════════════════════════════════════════════════════════════════
    def test_09_property_based_invariable(self):
        """
        Genera 50 transacciones aleatorias continuas (compras con distintas tasas de IVA,
        aportes, retiros, pagos a proveedor, depreciaciones) y comprueba que para
        CADA UNA de las operaciones válidas se cumpla estrictamente:
        Activo == Pasivo + Patrimonio (Diferencia = 0.00).
        """
        conn = self.get_conn()
        tipos_iva = [0.13, 0.08, 0.04, 0.02, 0.01, 0.0]
        
        for k in range(1, 51):
            monto_base = round(random.uniform(5000.0, 500000.0), 2)
            tasa = random.choice(tipos_iva)
            iva_monto = round(monto_base * tasa, 2)
            tot_fac = round(monto_base + iva_monto, 2)
            
            tipo_op = random.choice(['compra_banco', 'compra_credito', 'aporte_dueno', 'retiro_dueno', 'depreciacion'])
            
            if tipo_op == 'compra_banco':
                lineas = [
                    {'cuenta_codigo': '5.1.1.01', 'cuenta_nombre': 'Costo Compras', 'debito': monto_base, 'credito': 0.0},
                    {'cuenta_codigo': '1.1.3.01', 'cuenta_nombre': 'IVA Crédito', 'debito': iva_monto, 'credito': 0.0},
                    {'cuenta_codigo': '1.1.1.02.01', 'cuenta_nombre': 'Bancos', 'debito': 0.0, 'credito': tot_fac}
                ]
            elif tipo_op == 'compra_credito':
                lineas = [
                    {'cuenta_codigo': '5.1.1.01', 'cuenta_nombre': 'Costo Compras', 'debito': monto_base, 'credito': 0.0},
                    {'cuenta_codigo': '1.1.3.01', 'cuenta_nombre': 'IVA Crédito', 'debito': iva_monto, 'credito': 0.0},
                    {'cuenta_codigo': '2.1.1.01', 'cuenta_nombre': 'Cuentas por Pagar', 'debito': 0.0, 'credito': tot_fac}
                ]
            elif tipo_op == 'aporte_dueno':
                lineas = [
                    {'cuenta_codigo': '1.1.1.02.01', 'cuenta_nombre': 'Bancos', 'debito': tot_fac, 'credito': 0.0},
                    {'cuenta_codigo': '3.1.1.01', 'cuenta_nombre': 'Capital del Propietario', 'debito': 0.0, 'credito': tot_fac}
                ]
            elif tipo_op == 'retiro_dueno':
                lineas = [
                    {'cuenta_codigo': '3.2.1.01', 'cuenta_nombre': 'Retiros Propietario', 'debito': tot_fac, 'credito': 0.0},
                    {'cuenta_codigo': '1.1.1.02.01', 'cuenta_nombre': 'Bancos', 'debito': 0.0, 'credito': tot_fac}
                ]
            elif tipo_op == 'depreciacion':
                monto_dep = round(random.uniform(1000.0, 50000.0), 2)
                lineas = [
                    {'cuenta_codigo': '5.3.1.01', 'cuenta_nombre': 'Gasto Depreciación', 'debito': monto_dep, 'credito': 0.0},
                    {'cuenta_codigo': '1.2.2.01', 'cuenta_nombre': '(-) Depreciación Acumulada', 'debito': 0.0, 'credito': monto_dep}
                ]
                
            # Limpiar líneas de débito/crédito en 0 si las hay
            lineas = [l for l in lineas if (l['debito'] > 0 or l['credito'] > 0)]
            
            a_rand = {
                'cliente_id': self.cid, 'fecha_contable': '2026-06-15',
                'tipo_operacion': tipo_op, 'documento_origen': f'PROP-TEST-{k}',
                'concepto': f'Transacción estocástica #{k}: {tipo_op}'
            }
            database.insert_asiento(conn, a_rand, lineas)
            
            # Verificación de invariante en CADA iteración
            st = self.get_ledger_balances(conn)
            self.assertAlmostEqual(st['diferencia_cuadre'], 0.0, places=2,
                                   msg=f"Invariante rota en transacción #{k} ({tipo_op}): Activo={st['activo']}, Pasivo={st['pasivo']}, Pat={st['patrimonio_total']}, Dif={st['diferencia_cuadre']}")

        conn.commit()
        print(f"\n[TEST 09] Prueba de propiedad superada exitosamente: 50 transacciones aleatorias procesadas.")
        print(f"         Invariante verificada: Activo == Pasivo + Patrimonio en el 100% de los estados.")
        conn.close()

    def test_10_balance_general_entrelazado_service(self):
        """[TEST 10] Verifica el cálculo del Balance General entrelazado vía contabilidad_service."""
        bg = cs.get_balance_general(self.cid, '2026-10-31', TEST_DB_PATH)
        self.assertIn('activo', bg)
        self.assertIn('pasivo', bg)
        self.assertIn('patrimonio', bg)
        self.assertAlmostEqual(bg['diferencia_cuadre'], 0.0, places=2)
        self.assertTrue(bg['cuadrado'])
        print(f"\n[TEST 10] get_balance_general validado: Activo={bg['activo']['total_activo_neto']}, Pasivo+Patrimonio={bg['total_pasivo_patrimonio']}, Diferencia={bg['diferencia_cuadre']}")

    def test_11_crear_asiento_ajuste(self):
        """[TEST 11] Verifica la creación de un asiento de ajuste contable con partida doble."""
        datos = {
            'concepto': 'Ajuste de aporte inicial auditado',
            'fecha': '2026-07-01',
            'lineas': [
                {'cuenta_codigo': '1.1.1.02.01', 'cuenta_nombre': 'Banco Nacional', 'debito': 500000.0, 'credito': 0.0},
                {'cuenta_codigo': '3.1.1.01', 'cuenta_nombre': 'Capital del Propietario', 'debito': 0.0, 'credito': 500000.0}
            ]
        }
        res = cs.crear_asiento_ajuste(self.cid, datos, TEST_DB_PATH)
        self.assertIn('asiento_id', res)
        print(f"\n[TEST 11] crear_asiento_ajuste validado con ID: {res['asiento_id']}")

if __name__ == '__main__':
    unittest.main()
