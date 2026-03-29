
Actúa como un arquitecto de software frontend senior + UX/UI designer especializado en SaaS financieros.

Genera una aplicación web completa en un solo archivo HTML (HTML + CSS + JavaScript puro, sin frameworks) que represente un sistema contable llamado "FACTURAMI", con arquitectura de doble interfaz:

1. Cliente (frontend simplificado)
2. Contable (backoffice avanzado)

El código debe ser modular, limpio, escalable y con separación lógica de responsabilidades (aunque esté en un solo archivo).

========================================
🔷 ARQUITECTURA GENERAL
=======================

* Usar patrón SPA (Single Page Application)
* Navegación controlada por JavaScript (sin recarga)
* Manejo de estado con un objeto global tipo store:

const store = {
user: { role: "cliente" | "contable", name: "Luis" },
periodo: "Junio 2026",
data: {
ventas: [],
gastos: [],
resumen: {}
}
}

* Sistema de rutas interno:
  goTo("dashboard"), goTo("ventas"), etc.

* Separar visualmente en:
  renderCliente()
  renderContable()

========================================
🔷 1. INTERFAZ CLIENTE (UX SIMPLIFICADO)
========================================

Objetivo: reducir carga cognitiva al mínimo.

Diseñar como app fintech/mobile-first.

🔹 Layout:

* Header simple
* Sin sidebar complejo
* Cards grandes
* Botones de acción primaria visibles

🔹 Componentes obligatorios:

A) HEADER:

* Saludo dinámico: "Hola, Luis 👋"
* Selector de período (dropdown)

B) RESUMEN (CARDS):

* Ventas del mes
* Gastos del mes
* IVA estimado

Cada card:

* Icono
* Valor grande
* Texto explicativo corto

C) ACCIONES RÁPIDAS:
Botones grandes:

* Registrar venta
* Registrar gasto
* Generar declaración

D) ALERTAS (CONDICIONALES):

* Si faltan datos → warning
* Si todo OK → success

E) TABLA SIMPLE:
Columnas:

* Fecha
* Tipo
* Monto
* Estado

Máximo 5 registros visibles

🔹 FORMULARIOS (MODAL o CARD):

VENTA:

* input: monto (number)
* toggle: incluye IVA (boolean)
* botón guardar

GASTO:

* input: monto
* select: categoría (3 opciones)
* botón guardar

🔹 REGLAS:

* NO mostrar términos técnicos (ej: “no sujeto”, “proporcionalidad”)
* NO tablas complejas
* Auto-cálculo de IVA en background

========================================
🔷 2. INTERFAZ CONTABLE (BACKOFFICE)
====================================

Objetivo: control total + visibilidad completa

🔹 Layout:

* Sidebar fijo (oscuro)
* Topbar con acciones
* Grid dashboard

🔹 SIDEBAR:

* Dashboard
* Ingresos
* Compras
* Gastos
* Costos
* D-150
* Clientes

🔹 DASHBOARD:

* KPI cards (Ingresos, Compras, Gastos, IVA)
* Gráfico (simulado con divs o canvas)
* Tabla de transacciones recientes

🔹 MÓDULOS:
Cada módulo debe renderizar:

* Tabla con:

  * columnas IVA (13%, exento, etc.)
  * totales
* Botón:

  * Nuevo registro

🔹 D-150:
Dividir en 3 bloques:

1. Ingresos
2. Compras + gastos
3. Resultado final

Mostrar:

* IVA débito
* IVA crédito
* IVA a pagar

Botón:
[ Generar D-150 ]

🔹 CLIENTES:
Tabla con:

* Nombre
* Estado:

  * Pendiente
  * En revisión
  * Completado

========================================
🔷 3. SISTEMA DE ROLES
======================

Implementar toggle visible:

[ Vista Cliente ] [ Vista Contable ]

* Cambia store.user.role
* Re-renderiza UI completa

========================================
🔷 4. SISTEMA DE DATOS (MOCK)
=============================

Generar datos simulados:

* 5 ventas
* 5 gastos
* valores realistas en CRC (₡)

Funciones:

calcIVA(monto, tasa)
calcResumen()

========================================
🔷 5. DISEÑO (UI SYSTEM)
========================

Usar design system consistente:

🔹 Colores:
--bg: #f8fafc
--primary: #2563eb
--success: #16a34a
--danger: #dc2626
--text: #0f172a

🔹 Tipografía:

* Font: Inter o similar
* Jerarquía clara (h1, h2, small)

🔹 Estilos:

* border-radius: 16px
* sombras suaves
* spacing generoso

🔹 Interacciones:

* hover states
* transiciones (0.2s ease)
* botones con feedback visual

========================================
🔷 6. CÓDIGO (REQUISITOS)
=========================

* Código limpio y comentado

* Separar secciones:
  // STATE
  // ROUTER
  // RENDER CLIENTE
  // RENDER CONTABLE
  // COMPONENTS
  // UTILS

* NO usar librerías externas

* Todo funcional al abrir el HTML

========================================
🔷 7. OBJETIVO FINAL
====================

El resultado debe parecer:

* Un producto SaaS real
* Listo para demo con clientes
* Profesional y moderno
* Fácil para usuario no técnico
* Potente para contadores

Evitar:

* diseño genérico
* tablas saturadas
* UX confuso

Priorizar:

* claridad
* jerarquía visual
* experiencia del usuario

Genera el archivo completo listo para ejecutar.
