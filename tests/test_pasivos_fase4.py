import sys, os
sys.path.insert(0, os.path.abspath('.'))
import unittest
import math
import datetime
import db as database
import services.contabilidad_service as cs

class TestPasivosFinancierosFase4(unittest.TestCase):

    def setUp(self):
        self.cliente_id = 'test_cliente_niif'
        # Asegurar cliente en BD para cumplir FK y limpiar asientos previos
        with database.get_db() as conn:
            conn.execute("INSERT OR IGNORE INTO clientes (id, nombre) VALUES (?, ?)", (self.cliente_id, "Cliente Pruebas NIIF"))
            conn.execute("DELETE FROM asiento_lineas WHERE asiento_id IN (SELECT id FROM asientos WHERE cliente_id=?)", (self.cliente_id,))
            conn.execute("DELETE FROM asientos WHERE cliente_id=?", (self.cliente_id,))

    def tearDown(self):
        with database.get_db() as conn:
            conn.execute("DELETE FROM asiento_lineas WHERE asiento_id IN (SELECT id FROM asientos WHERE cliente_id=?)", (self.cliente_id,))
            conn.execute("DELETE FROM asientos WHERE cliente_id=?", (self.cliente_id,))
            conn.execute("DELETE FROM clientes WHERE id=?", (self.cliente_id,))

    def test_a_prestamo_12m_12pct_cuota_nivelada(self):
        """
        Prueba a) Préstamo de ₡12 000 000, 12 meses, 12% anual, cuota nivelada:
        12 cuotas; interés cuota 1 = ₡120 000,00; cuota ≈ ₡1 066 185,50;
        suma de capital = ₡12 000 000,00; saldo final = ₡0,00.
        """
        res = cs.calcular_tabla_amortizacion(
            principal=12000000.0,
            tasa_anual=12.0,
            plazo_meses=12,
            periodicidad='mensual',
            sistema='frances',
            fecha_inicio='2026-10-01',
            dia_pago=1
        )
        cuotas = res['cuotas']
        self.assertEqual(len(cuotas), 12, "Debe tener exactamente 12 cuotas")
        
        c1 = cuotas[0]
        self.assertAlmostEqual(c1['interes'], 120000.00, places=2, msg="Interés cuota 1 debe ser ₡120 000.00")
        self.assertAlmostEqual(c1['cuota_total'], 1066185.46, places=1, msg="Cuota debe ser ≈ ₡1 066 185.50")
        
        c_last = cuotas[-1]
        self.assertEqual(c_last['saldo_final'], 0.0, "Saldo final de la última cuota debe ser exactamente 0.00")
        
        tot_cap = round(sum(c['amortizacion_capital'] for c in cuotas), 2)
        self.assertEqual(tot_cap, 12000000.00, "Suma de amortizaciones de capital debe ser exactamente ₡12 000 000.00")

    def test_b_desembolso_a_bancos_cuadre_balance(self):
        """
        Prueba b) Desembolso a Bancos: Bancos +12M, Préstamo +12M, balance cuadrado.
        """
        datos = {
            'monto_principal': 12000000.0,
            'tasa_anual': 12.0,
            'plazo_meses': 12,
            'periodicidad': 'mensual',
            'sistema': 'frances',
            'fecha_inicio': '2026-10-01',
            'contrapartida': 'banco',
            'cuenta_banco_codigo': '1.1.1.02.01',
            'acreedor': 'BNCR'
        }
        pasivo = cs.registrar_pasivo_financiero(self.cliente_id, datos)
        self.assertIsNotNone(pasivo['asiento_desembolso_id'])
        
        bg = cs.get_balance_general(self.cliente_id, '2026-10-31')
        self.assertTrue(bg['cuadrado'], "Balance debe estar cuadrado tras desembolso")
        self.assertEqual(bg['activo']['total_corriente'], 12000000.00)
        self.assertEqual(bg['pasivo']['total_no_corriente'], 12000000.00)
        self.assertEqual(bg['diferencia_cuadre'], 0.0)

    def test_c_pago_cuota_1(self):
        """
        Prueba c) Pago de la cuota 1: Bancos baja por la cuota, capital baja, gasto por intereses = ₡120 000.
        """
        datos = {
            'monto_principal': 12000000.0,
            'tasa_anual': 12.0,
            'plazo_meses': 12,
            'periodicidad': 'mensual',
            'sistema': 'frances',
            'fecha_inicio': '2026-10-01',
            'contrapartida': 'banco',
            'cuenta_banco_codigo': '1.1.1.02.01'
        }
        pasivo = cs.registrar_pasivo_financiero(self.cliente_id, datos)
        
        # Pago cuota 1
        datos_pago = {
            'tipo': 'cuota_ordinaria',
            'monto_total': 1066185.46,
            'fecha': '2026-11-01',
            'origen_fondos': 'banco',
            'cuenta_banco_codigo': '1.1.1.02.01',
            'num_cuota': 1
        }
        res_pago = cs.registrar_pago_pasivo(self.cliente_id, pasivo, datos_pago)
        pago = res_pago['pago']
        self.assertEqual(pago['monto_interes'], 120000.00)
        self.assertAlmostEqual(pago['monto_capital'], 946185.46, places=2)
        
        bg = cs.get_balance_general(self.cliente_id, '2026-11-30')
        self.assertTrue(bg['cuadrado'], "Balance debe estar cuadrado tras pago cuota 1")
        self.assertAlmostEqual(bg['patrimonio']['utilidad_ejercicio'], -120000.00, places=2)

    def test_d_abono_extraordinario_2m_plazo_y_cuota(self):
        """
        Prueba d) Abono extraordinario de ₡2 000 000 después de la cuota 3:
        Probar opción reducir plazo y opción reducir cuota.
        El saldo es consistente, intereses totales menores y versión anterior conservada.
        """
        res_plan = cs.calcular_tabla_amortizacion(12000000.0, 12.0, 12, 'mensual', 'frances', '2026-10-01')
        cuotas_orig = res_plan['cuotas']
        int_orig = sum(c['interes'] for c in cuotas_orig)
        
        # Reducir Plazo
        rec_plazo = cs.recalcular_por_abono_extraordinario(
            cuotas_anteriores=cuotas_orig,
            cuota_corte_idx=2, # Después de cuota 3
            monto_abono=2000000.0,
            tipo_recalculo='reducir_plazo'
        )
        self.assertLess(rec_plazo['num_cuotas_totales'], 12, "Debe reducir el número de cuotas")
        self.assertLess(rec_plazo['tot_interes_nuevo'], int_orig, "Intereses deben ser menores que el plan original")
        self.assertGreater(rec_plazo['ahorro_intereses'], 0, "Debe haber ahorro en intereses")
        
        # Reducir Cuota
        rec_cuota = cs.recalcular_por_abono_extraordinario(
            cuotas_anteriores=cuotas_orig,
            cuota_corte_idx=2,
            monto_abono=2000000.0,
            tipo_recalculo='reducir_cuota'
        )
        self.assertEqual(rec_cuota['num_cuotas_totales'], 12, "Debe mantener 12 cuotas")
        self.assertLess(rec_cuota['cuotas'][3]['cuota_total'], cuotas_orig[0]['cuota_total'], "La nueva cuota debe ser menor")
        self.assertLess(rec_cuota['tot_interes_nuevo'], int_orig)
        self.assertGreater(rec_cuota['ahorro_intereses'], 0)

    def test_e_cancelacion_total_anticipada(self):
        """
        Prueba e) Cancelación total anticipada: saldo 0, estado Cancelado, asiento correcto.
        """
        datos = {
            'monto_principal': 5000000.0,
            'tasa_anual': 10.0,
            'plazo_meses': 6,
            'contrapartida': 'banco',
            'cuenta_banco_codigo': '1.1.1.02.01'
        }
        pasivo = cs.registrar_pasivo_financiero(self.cliente_id, datos)
        
        datos_cancel = {
            'tipo': 'cancelacion_total',
            'monto_total': 5000000.0,
            'fecha': '2026-10-15',
            'origen_fondos': 'banco',
            'cuenta_banco_codigo': '1.1.1.02.01'
        }
        res = cs.registrar_pago_pasivo(self.cliente_id, pasivo, datos_cancel)
        self.assertEqual(pasivo['saldo_capital_actual'], 0.0)
        self.assertEqual(pasivo['estado'], 'cancelado')
        
        bg = cs.get_balance_general(self.cliente_id, '2026-10-31')
        self.assertTrue(bg['cuadrado'])
        self.assertEqual(bg['pasivo']['total_no_corriente'], 0.0)

    def test_f_anulacion_pago_reversion_y_restauracion(self):
        """
        Prueba f) Anulación del abono/pago: asiento inverso y plan restaurado.
        """
        datos = {
            'monto_principal': 6000000.0,
            'tasa_anual': 12.0,
            'plazo_meses': 6,
            'contrapartida': 'banco',
            'cuenta_banco_codigo': '1.1.1.02.01'
        }
        pasivo = cs.registrar_pasivo_financiero(self.cliente_id, datos)
        saldo_antes = pasivo['saldo_capital_actual']
        
        # Abono
        res_abono = cs.registrar_pago_pasivo(self.cliente_id, pasivo, {
            'tipo': 'abono_extraordinario_plazo',
            'monto_total': 1000000.0,
            'fecha': '2026-11-01',
            'origen_fondos': 'banco',
            'cuenta_banco_codigo': '1.1.1.02.01'
        })
        self.assertEqual(pasivo['version_activa'], 2)
        
        # Anular abono
        res_anul = cs.anular_pago_pasivo(self.cliente_id, pasivo, res_abono['pago']['id'], "Error de digitación")
        self.assertEqual(pasivo['version_activa'], 1, "Debe restaurar versión 1 del plan")
        self.assertEqual(pasivo['saldo_capital_actual'], saldo_antes, "Debe restaurar el saldo previo")

    def test_h_sistemas_adicionales_y_fin_de_mes(self):
        """
        Prueba h) Sistemas adicionales: alemán, bullet, sin intereses, gracia y día 31 en meses cortos.
        """
        # Alemán
        res_aleman = cs.calcular_tabla_amortizacion(6000000.0, 12.0, 6, sistema='aleman')
        self.assertEqual(len(res_aleman['cuotas']), 6)
        self.assertEqual(res_aleman['cuotas'][0]['amortizacion_capital'], 1000000.00)
        self.assertEqual(res_aleman['cuotas'][-1]['saldo_final'], 0.0)
        
        # Bullet
        res_bullet = cs.calcular_tabla_amortizacion(1000000.0, 12.0, 3, sistema='bullet')
        self.assertEqual(res_bullet['cuotas'][0]['amortizacion_capital'], 0.0)
        self.assertEqual(res_bullet['cuotas'][-1]['amortizacion_capital'], 1000000.0)
        self.assertEqual(res_bullet['cuotas'][-1]['saldo_final'], 0.0)
        
        # Sin intereses
        res_sin_int = cs.calcular_tabla_amortizacion(1200000.0, 0.0, 4, sistema='sin_interes')
        self.assertEqual(res_sin_int['tot_interes'], 0.0)
        self.assertEqual(res_sin_int['cuotas'][-1]['saldo_final'], 0.0)
        
        # Fin de mes día 31 (ej. febrero debe dar día 28 o 29)
        res_feb = cs.calcular_tabla_amortizacion(1000000.0, 12.0, 6, fecha_inicio='2026-10-31', dia_pago=31)
        fechas = [c['fecha_vencimiento'] for c in res_feb['cuotas']]
        # Noviembre debe dar 2026-11-30, Febrero debe dar 2027-02-28
        self.assertTrue(any(f.endswith('-11-30') for f in fechas))
        self.assertTrue(any(f.endswith('-02-28') for f in fechas))




    def test_escenario_base_cero(self):
        """
        Escenario base, partiendo de cero:
        a) Aporte inicial ₡20 000 000: Bancos 20M, Capital 20M.
        b) Compra de vehículo ₡16 000 000 desde Bancos: Bancos 4M, Vehículos 16M.
        c) Compra de maquinaria ₡1 000 000 a crédito: Cuentas por pagar 1M.
        d) Depreciación de ₡100 000: Gasto 100k, Depreciación acumulada 100k.
        Resultado esperado: Activo ₡20 900 000 = Pasivo ₡1 000 000 + Patrimonio ₡19 900 000. Diferencia = 0.
        """
        with database.get_db() as conn:
            # a) Aporte inicial 20M
            database.insert_asiento(conn, {
                'cliente_id': self.cliente_id, 'fecha_contable': '2026-01-01',
                'concepto': 'Aporte inicial de capital', 'tipo_operacion': 'aporte_capital'
            }, [
                {'cuenta_codigo': '1.1.1.02.01', 'cuenta_nombre': 'Bancos BNCR', 'debito': 20000000.0, 'credito': 0.0},
                {'cuenta_codigo': '3.1.1.01', 'cuenta_nombre': 'Capital del Propietario (Aportes)', 'debito': 0.0, 'credito': 20000000.0}
            ])

            # b) Compra vehículo 16M desde bancos
            database.insert_asiento(conn, {
                'cliente_id': self.cliente_id, 'fecha_contable': '2026-01-05',
                'concepto': 'Compra de Vehículo de operaciones', 'tipo_operacion': 'compra_activo'
            }, [
                {'cuenta_codigo': '1.2.1.01', 'cuenta_nombre': 'Vehículos', 'debito': 16000000.0, 'credito': 0.0},
                {'cuenta_codigo': '1.1.1.02.01', 'cuenta_nombre': 'Bancos BNCR', 'debito': 0.0, 'credito': 16000000.0}
            ])

            # c) Compra maquinaria 1M a crédito
            database.insert_asiento(conn, {
                'cliente_id': self.cliente_id, 'fecha_contable': '2026-01-10',
                'concepto': 'Compra de Maquinaria y Herramientas a crédito', 'tipo_operacion': 'compra_activo_credito'
            }, [
                {'cuenta_codigo': '1.2.1.02', 'cuenta_nombre': 'Maquinaria y Herramientas', 'debito': 1000000.0, 'credito': 0.0},
                {'cuenta_codigo': '2.1.1.01', 'cuenta_nombre': 'Cuentas por Pagar Proveedores', 'debito': 0.0, 'credito': 1000000.0}
            ])

            # d) Depreciación 100k
            database.insert_asiento(conn, {
                'cliente_id': self.cliente_id, 'fecha_contable': '2026-01-31',
                'concepto': 'Depreciación mensual enero 2026', 'tipo_operacion': 'depreciacion'
            }, [
                {'cuenta_codigo': '5.2.1.02', 'cuenta_nombre': 'Gasto por Depreciación', 'debito': 100000.0, 'credito': 0.0},
                {'cuenta_codigo': '1.2.2.01', 'cuenta_nombre': 'Depreciación Acumulada', 'debito': 0.0, 'credito': 100000.0}
            ])

        # Verificación del Balance General
        bg = cs.get_balance_general(self.cliente_id, '2026-01-31')
        
        # Activo: Bancos (4M) + Vehículos (16M) + Maquinaria (1M) - Depre (100k) = 20 900 000
        self.assertEqual(bg['activo']['total_corriente'], 4000000.0, "Bancos debe tener ₡4 000 000.00")
        self.assertEqual(bg['activo']['total_no_corriente_bruto'], 17000000.0, "Activos fijos brutos deben ser ₡17 000 000.00")
        self.assertEqual(bg['activo']['depreciacion_acumulada'], 100000.0, "Depreciación acumulada debe ser ₡100 000.00")
        self.assertEqual(bg['activo']['total_activo_neto'], 20900000.0, "Total Activo Neto debe ser exactamente ₡20 900 000.00")

        # Pasivo: Cuentas por pagar = 1 000 000
        self.assertEqual(bg['pasivo']['total_corriente'], 1000000.0, "Total Pasivo debe ser ₡1 000 000.00")
        self.assertEqual(bg['pasivo']['total_pasivo'], 1000000.0)

        # Patrimonio: Capital (20M) + Utilidad (-100k) = 19 900 000
        self.assertEqual(bg['patrimonio']['capital'], 20000000.0)
        self.assertEqual(bg['patrimonio']['utilidad_ejercicio'], -100000.0)
        self.assertEqual(bg['patrimonio']['total_patrimonio'], 19900000.0, "Total Patrimonio debe ser ₡19 900 000.00")

        # Total Pasivo y Patrimonio = 20 900 000
        self.assertEqual(bg['total_pasivo_patrimonio'], 20900000.0)
        self.assertEqual(bg['diferencia_cuadre'], 0.0, "La diferencia de cuadre debe ser exactamente 0.00")
        self.assertTrue(bg['cuadrado'])
        print("[OK] ESCENARIO BASE CERO COMPLETO Y VERIFICADO: Activo 20.9M = Pasivo 1.0M + Patrimonio 19.9M (Diferencia = 0.00)")

if __name__ == '__main__':
    unittest.main()
