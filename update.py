import re

with open('d:/Sistema contable/FACTURAMI.html', 'r', encoding='utf-8') as f:
    html = f.read()

# 1. Add PapaParse
html = html.replace(
    '<link href="https://fonts.googleapis.com/css2?', 
    '<script src="https://cdnjs.cloudflare.com/ajax/libs/PapaParse/5.4.1/papaparse.min.js"></script>\n<link href="https://fonts.googleapis.com/css2?'
)

# 2. Main Mesh Gradient
html = html.replace(
    '.main{flex:1;height:100vh;overflow-y:auto;background:var(--cream)}',
    '.main{flex:1;height:100vh;overflow-y:auto;background:var(--cream);position:relative;}\n.main::before{content:\'\';position:fixed;top:-100px;left:-100px;width:600px;height:600px;border-radius:50%;background:radial-gradient(circle,rgba(10,124,82,0.06),transparent 60%);filter:blur(80px);pointer-events:none;z-index:0;transition:background 0.8s ease;}\n.main.debt::before{background:radial-gradient(circle,rgba(200,30,30,0.06),transparent 60%);}'
)

# 3. Glassmorphism variables
html = html.replace(
    '--white:#ffffff;',
    '--white:rgba(255,255,255,0.7); /* Glass base */'
)
html = html.replace(
    '--shadow:0 1px 3px rgba(0,0,0,.06),0 4px 16px rgba(0,0,0,.06);',
    '--shadow:0 8px 32px rgba(31,38,135,0.06);'
)

# Glassmorphism on topbar, cards, kpis
html = html.replace(
    'background:var(--white);\n  border-bottom:1px solid var(--cream3);',
    'background:rgba(255,255,255,0.65);\n  backdrop-filter:blur(24px);-webkit-backdrop-filter:blur(24px);\n  border-bottom:1px solid rgba(255,255,255,0.8);'
)
html = html.replace(
    'background:var(--white);border:1px solid var(--cream3);border-radius:var(--r2);\n  padding:20px 22px;box-shadow:var(--shadow);transition:all .22s;',
    'background:var(--white);border:1px solid rgba(255,255,255,0.8);border-radius:var(--r2);\n  padding:20px 22px;box-shadow:var(--shadow);transition:all .22s;\n  backdrop-filter:blur(16px);-webkit-backdrop-filter:blur(16px);position:relative;z-index:1;'
)
html = html.replace(
    'background:var(--white);border:1px solid var(--cream3);border-radius:var(--r2);\n  padding:18px 20px;box-shadow:var(--shadow);transition:all .22s;position:relative;overflow:hidden;',
    'background:var(--white);border:1px solid rgba(255,255,255,0.8);border-radius:var(--r2);\n  padding:18px 20px;box-shadow:var(--shadow);transition:all .22s;position:relative;overflow:hidden;\n  backdrop-filter:blur(16px);-webkit-backdrop-filter:blur(16px);z-index:1;'
)

# 4. CSV Button and side panel HTML
html = html.replace(
    '<button class="top-btn" onclick="showToast(\'Exportando datos...\')">⬇ Exportar</button>',
    '<button class="top-btn" onclick="document.getElementById(\'csvUploader\').click()">📥 Importar CSV Fiscal</button>\n      <button class="top-btn" onclick="showToast(\'Exportando datos...\')">⬇ Exportar</button>'
)

html = html.replace(
    '<!-- ════════════ DASHBOARD ═══════════════════════════════════ -->\n  <div class="page on" id="pg-dash">',
    '<!-- ════════════ DASHBOARD ═══════════════════════════════════ -->\n  <div class="page on" id="pg-dash">\n    <input type="file" id="csvUploader" accept=".csv" style="display:none" onchange="importData(event)">'
)

chart_section_old = """    <!-- Charts -->
    <div class="g2" style="margin-bottom:18px">"""
chart_section_new = """    <!-- Charts and Alerts -->
    <div style="display:grid;grid-template-columns:2fr 1fr;gap:16px;margin-bottom:18px;position:relative;z-index:1">
      <div style="display:flex;flex-direction:column;gap:16px">"""
if chart_section_old in html:
    html = html.replace(chart_section_old, chart_section_new)

tx_section_old = """    <!-- Transactions -->
    <div class="card">"""
tx_section_new = """      </div> <!-- End Left Column -->
      
      <!-- Right Column: Tributin Alerts -->
      <div class="card" style="display:flex;flex-direction:column">
        <div class="sh" style="margin-bottom:12px">
          <div><div class="sh-t" style="display:flex;align-items:center;gap:6px">🤖 Tributín QA <span class="badge badge-r" id="alertCount" style="display:none">0</span></div><div class="sh-s">Auditoría en tiempo real</div></div>
        </div>
        <div id="tributinFeed" style="display:flex;flex-direction:column;gap:8px;flex:1;overflow-y:auto;max-height:300px">
           <div style="font-size:11px;color:var(--ink3);text-align:center;margin:auto;padding:20px">Sin inconsistencias. Importa un CSV para auditar.</div>
        </div>
      </div>
    </div>

    <!-- Transactions -->
    <div class="card" style="position:relative;z-index:1">"""
if tx_section_old in html:
    html = html.replace(tx_section_old, tx_section_new)

# 5. JS Logic additions
js_additions = """
function importData(e) {
  const file = e.target.files[0];
  if(!file) return;
  showToast('Analizando CSV fiscal...');
  Papa.parse(file, {
    header: true, skipEmptyLines: true, dynamicTyping: true,
    complete: function(res) {
      if(!res.data.length){ showToast('CSV vacío o inválido'); return; }
      processImport(res.data);
    }
  });
}

function processImport(data) {
  let ng = 0, n_alert = 0;
  const tFeed = document.getElementById('tributinFeed');
  tFeed.innerHTML = '';
  
  data.forEach(row => {
    const tipo = (row.TipoDoc || '').toString().toLowerCase();
    const iva = parseFloat(row.ImpuestoNeto || row.IVA || 0);
    const tot = parseFloat(row.TotalComprobante || row.Total || 0);
    const baseG = parseFloat(row.MontoTotalGravado || row.Gravado || 0);
    const cabys = (row.CodigoCABYS || '').toString();
    const t_iva = parseFloat(row.TarifaIVA || 0);
    
    let tp_disp = 'Gasto';
    if(tipo.includes('ingreso') || tipo.includes('venta') || row.TotalComprobante > 0 && !tipo) tp_disp = 'Ingreso';
    if(tipo.includes('gasto') || tipo.includes('compra')) tp_disp = 'Gasto';
    
    if(ng < 10) {
      TXS.unshift({
        f: row.FechaEmision || '15/06/2026', c: (row.ClaveHacienda||'000...').toString().substring(0,20)+'...',
        tp: tp_disp, d: row.NombreRazonSocial || 'Recibo DTE',
        ex: fmt(row.MontoTotalExento||0), g: fmt(baseG),
        iva: fmt(iva), tot: fmt(tot), st: 'Procesado'
      });
      ng++;
    }
    
    if(tp_disp === 'Gasto' && t_iva === 13 && cabys.startsWith('0')) {
      n_alert++;
      tFeed.innerHTML += `
        <div style="padding:10px;background:var(--amber-l);border-right:3px solid var(--amber);border-radius:8px">
          <div style="font-size:10px;font-weight:600;color:var(--amber);margin-bottom:2px">Alerta CABYS</div>
          <div style="font-size:11px;color:var(--ink2)">Gasto de <b>${fmt(tot)}</b> de <i>${row.NombreRazonSocial||'N/A'}</i> tiene IVA 13% pero CABYS es irregular (Materia prima).</div>
        </div>`;
    }
    
    if(tp_disp === 'Ingreso'){ D.ingresos += baseG; }
    else { D.compras += (baseG*0.7); D.gastos += (baseG*0.3); }
  });
  
  D.iva = (D.ingresos * 0.13) - ((D.compras + D.gastos) * 0.13); 
  
  if(n_alert > 0){
    document.getElementById('alertCount').textContent = n_alert;
    document.getElementById('alertCount').style.display = 'inline-flex';
  } else {
    tFeed.innerHTML = '<div style="font-size:11px;color:var(--ink3);text-align:center;margin:auto;padding:20px">Facturas consistentes. Todo en orden.</div>';
    document.getElementById('alertCount').style.display = 'none';
  }
  
  if(D.iva > 1500000) document.querySelector('.main').classList.add('debt');
  else document.querySelector('.main').classList.remove('debt');
  
  initDash();
  if(document.getElementById('pg-d150').classList.contains('on')) renderD150();
  showToast(`¡Papaparse ha cargado ${data.length} docs DTE!`);
}
"""
if "processImport" not in html:
    html = html.replace('// ── INIT DASHBOARD ───────────────────────────────────────────────', js_additions + '\n// ── INIT DASHBOARD ───────────────────────────────────────────────')

with open('d:/Sistema contable/FACTURAMI.html', 'w', encoding='utf-8') as f:
    f.write(html)
print("Updated HTML successfully")
