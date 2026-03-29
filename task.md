# Tareas de Ejecución: FACTURAMI Premium & Fiscal

- [x] 1. Configurar Infraestructura y Dependencias
    - [x] Importar PapaParse vía CDN.
    - [x] Añadir marcadores HTML para el input de CSV oculto.
- [x] 2. Implementar Glassmorfismo y Estética
    - [x] Modificar CSS para `.main` (fondo de malla/mesh gradient con variables de estado).
    - [x] Actualizar `.card`, `.kpi`, `.top` para usar `backdrop-filter: blur` y colores translúcidos (rgba).
    - [x] Refinar animaciones de transiciones de las tarjetas.
- [x] 3. Desarrollar Motor de Importación (PapaParse + CR Fiscal)
    - [x] Manejador del input CSV `onChange`.
    - [x] Mapeo de columnas: `FechaEmision`, `ClaveHacienda`, `Identificacion`, `NombreRazonSocial`, `CodigoCABYS`, `MontoTotalExento`, `MontoTotalGravado`, `TarifaIVA`, `ImpuestoNeto`, `TotalComprobante`, `TipoDoc`, `CondicionVenta`.
    - [x] Lógica para clasificar la data en el Store de la aplicación (`D` y `TXS`).
- [x] 4. Visualización y Alertas "Tributín"
    - [x] Agregar panel lateral de "Inconsistencias y Alertas" de validación fiscal al dashboard.
    - [x] Lógica de validación (heurística) en la importación.
    - [x] Animaciones fluidas en gráficos (Donut `stroke-dasharray` transition, etc.).
- [x] 5. Verificación
    - [x] Crear archivo CSV de prueba (`facturas_test.csv`) estructurado y validar importación.
    - [x] Visualizar estética en el entorno local.
