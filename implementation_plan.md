# Plan de Continuación: Mejoras Premium y Adaptación Fiscal para FACTURAMI SaaS

Tu propuesta para mejorar la fidelidad visual y operativa del dashboard es excelente. Llevar los componentes a un estado verdaderamente "glassmórfico" y habilitar la ingesta dinámica de datos transformará la maqueta en un prototipo funcional de alto impacto.

Con base en la investigación tributaria sobre **TRIBU-CR**, he complementado tus puntos para que esta actualización no sea sólo estética, sino 100% alineada a la realidad fiscal costarricense.

## User Review Required
> [!IMPORTANT]
> **Adopción del Estándar XML vs CSV**
> En Costa Rica, el estándar absoluto es el XML de Facturación Electrónica (v4.3) emitido por Hacienda. Aunque me propones importar CSV (lo cual es perfecto para un prototipo rápido), propongo que el formato de ese CSV simule **exactamente** los atributos que contendría un XML tico. ¿Estás de acuerdo con estructurar el parser CSV con las columnas propuestas abajo para que el salto futuro a XML real sea fluido?

## Mejoras y Complementos Propuestos (Basados en Investigación)

### 1. Glassmorfismo Inteligente (Responsive al Estado Fiscal)
*   **Aporte:** Además de aplicar `backdrop-filter: blur(16px)` y mallas de gradiente sutiles bajo la `.main`, propongo vincular el tinte del cristal al **estado de la declaración activa**.
*   **Ejemplo:** Si el IVA a declarar es alto (Deuda), la "malla" de fondo en el dashboard emitirá un tinte ámbar translúcido. Si hay saldo a favor, emitirá un verde/azul suave (Cream/Ink estético).

### 2. Estructura del Parser CSV (Alineado a D-150 / D-101)
*   **Aporte:** La función `importCSV(modulo, file)` no debe buscar columnas genéricas. Debe reconocer el vocabulario del Ministerio de Hacienda.
*   **Columnas Sugeridas para el Parser:**
    1.  `FechaEmision` (dd/mm/yyyy)
    2.  `ClaveHacienda` (String 50 caracteres)
    3.  `Identificacion` (Cédula Física/Jurídica/DIMEX)
    4.  `NombreRazonSocial`
    5.  `CodigoCABYS` (Vital para cruzar con D-101)
    6.  `MontoTotalExento`
    7.  `MontoTotalGravado`
    8.  `TarifaIVA` (13%, 4%, 2%, 1%)
    9.  `ImpuestoNeto`
    10. `TotalComprobante`

### 3. Visualización y Alertas "Tributín"
*   **Aporte:** Agregaremos al lado de los gráficos un panel dinámico de **"Inconsistencias y Alertas"**. Al importar el CSV, la lógica evaluará las filas y generará alertas como: *"Detectamos 2 facturas gravadas con CABYS no afín a tu actividad (Prorrata generada)"*.
*   Los gráficos (especialmente la dona de "Crédito Fiscal") se animarán usando `stroke-dasharray` mediante una transición `ease-in-out` de CSS vinculada a los JS `setTimeout`.

## Cambios Propuestos en Código

---
### FACTURAMIV2 (2).html

#### [MODIFY] Estilos CSS (`<style>`)
*   Se añadirán `div` absolutos con `filter: blur(100px)` y colores pastel desaturados detrás del `<main>` para generar el efecto *Mesh Gradient*.
*   Las clases `.card, .kpi, .top, .sb-user` migrarán de fondos opacos a fondos con variables rgba (ej. `background: rgba(255, 255, 255, 0.45); border: 1px solid rgba(255,255,255,0.6); backdrop-filter: blur(20px); -webkit-backdrop-filter: blur(20px);`).
*   Se configurará el *smooth-scroll* y sticky position mejorado en `.top`.

#### [MODIFY] Markup HTML (`<body>`)
*   Adición del input file invisible `<input type="file" id="csvUploader" accept=".csv" style="display:none">`.
*   Botones de módulo conectados triggeando `document.getElementById('csvUploader').click()`.
*   Un nuevo div lateral en el Dashboard: `<div class="card glass-alert">` para el panel heurístico (Tributín).

#### [MODIFY] Script JS (`<script>`)
*   Implementación de `importCSV(e)`. Lectura asíncrona mediante `FileReader()`.
*   Un micro-parser que separe filas por `\n` y columnas por `,` o `;`.
*   Función mapeadora que inyecte el HTML dinámico de las nuevas transacciones en la tabla y recalcule los totales de la constante `D` en tiempo real.
*   Lógica de animación para los SVGs recargando los `stroke-dashoffset`.

## Open Questions
> [!WARNING]
> ¿Prefieres que utilice una librería externa ligera nativa (como `PapaParse` vía CDN) para no reinventar el analizador de CSV, o prefieres un parser vanilla en JavaScript puro que mantenga el archivo en cero dependencias absolutas?

## Plan de Verificación
### Pruebas Manuales
1. Crear un archivo `test_facturas_costa_rica.csv` con columnas estructuradas.
2. Hacer clic en "Importar CSV" en la aplicación.
3. Verificar que las tarjetas KPI actualizan sus montos al instante.
4. Auditar visualmente el efecto Glassmorphism simulando un scroll a lo largo de las tablas para confirmar que el desenfoque es fluido.
