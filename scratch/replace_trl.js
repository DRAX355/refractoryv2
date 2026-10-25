const fs = require('fs');

let content = fs.readFileSync('ui/app.html', 'utf8');

// 1. CSS
content = content.replace('.t-trl {', '.t-nvx,\n    .t-trl {');

// 2. getApiBase dynamic query param
const old_api = `    /* ============ API CONFIGURATION ============ */
    function getApiBase() {
      if (typeof window !== 'undefined' && window.REFRACTORY_API_BASE) {
        return window.REFRACTORY_API_BASE.replace(/\\/+$/, '');
      }
      try {
        const saved = localStorage.getItem('refractory_api_base');
        if (saved) return saved.replace(/\\/+$/, '');
      } catch (e) {}

      if (typeof window !== 'undefined' && window.location && window.location.protocol.startsWith('http')) {
        // If frontend is running on dev port 3000
        if (window.location.port === '3000') {
          const isLocal = window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1';
          return \`\${window.location.protocol}//\${window.location.hostname}:\${isLocal ? '8000' : '8030'}\`;
        }
        // If frontend is served directly by FastAPI (e.g. http://<host>:8000/ui/) or reverse proxy
        return window.location.origin;
      }
      return 'http://localhost:8000';
    }`;

const new_api = `    /* ============ API CONFIGURATION (Dynamic, Zero Hardcoding) ============ */
    function getApiBase() {
      // 1. Explicit window override
      if (typeof window !== 'undefined' && window.REFRACTORY_API_BASE) {
        return window.REFRACTORY_API_BASE.replace(/\\/+$/, '');
      }
      // 2. Query param ?api=https://...
      try {
        if (typeof window !== 'undefined' && window.location && window.location.search) {
          const qApi = new URLSearchParams(window.location.search).get('api');
          if (qApi) {
            localStorage.setItem('refractory_api_base', qApi);
            return qApi.replace(/\\/+$/, '');
          }
        }
      } catch (e) {}
      // 3. Saved localStorage
      try {
        const saved = localStorage.getItem('refractory_api_base');
        if (saved) return saved.replace(/\\/+$/, '');
      } catch (e) {}

      // 4. Inferred from current browser origin
      if (typeof window !== 'undefined' && window.location && window.location.protocol.startsWith('http')) {
        // If frontend is running on dev port 3000
        if (window.location.port === '3000') {
          const isLocal = window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1';
          return \`\${window.location.protocol}//\${window.location.hostname}:\${isLocal ? '8000' : '8030'}\`;
        }
        // If frontend is served directly by FastAPI or reverse proxy
        return window.location.origin;
      }
      return 'http://localhost:8000';
    }`;

content = content.replace(old_api, new_api);

// 3. Constants and Grades
content = content.replace(
    "const GRADE_LIST = ['TRL HMR C2R', 'TRL HMR C3R', 'TRL HMR C4', 'TRL HMR C5R', 'TRL MC-4', 'Steel-clad (SCB)'];",
    "const GRADE_LIST = ['NVX HMR C2R', 'NVX HMR C3R', 'NVX HMR C4', 'NVX HMR C5R', 'NVX MC-4', 'Steel-clad (SCB)'];"
);

content = content.replace(
    "const GCOL = { 'TRL HMR C2R': '#6cae3e', 'TRL HMR C3R': '#2f5fd0', 'TRL HMR C4': '#b8892c', 'TRL HMR C5R': '#f08c86', 'TRL MC-4': '#35b6e8', 'Steel-clad (SCB)': '#8a8f98' };",
    "const GCOL = { 'NVX HMR C2R': '#6cae3e', 'NVX HMR C3R': '#2f5fd0', 'NVX HMR C4': '#b8892c', 'NVX HMR C5R': '#f08c86', 'NVX MC-4': '#35b6e8', 'Steel-clad (SCB)': '#8a8f98' };"
);

content = content.replace(
    "function gcol(g) { if (GCOL[g]) return GCOL[g];",
    "function gcol(g) { const norm = String(g).replace(/^TRL\\\\s+/, 'NVX '); if (GCOL[norm]) return GCOL[norm]; if (GCOL[g]) return GCOL[g];"
);

// 4. Grades & products in single quotes
content = content.split("'TRL HMR C2R'").join("'NVX HMR C2R'");
content = content.split("'TRL MC-4'").join("'NVX MC-4'");
content = content.split("'TRL HMR C5R'").join("'NVX HMR C5R'");
content = content.split("'TRL HMR C3R'").join("'NVX HMR C3R'");
content = content.split("'TRL HMR C4'").join("'NVX HMR C4'");
content = content.split("'TRL HCH111B'").join("'NVX HCH111B'");
content = content.split("'TRL RAM M-95'").join("'NVX RAM M-95'");
content = content.split("'TRL nozzle block'").join("'NVX nozzle block'");
content = content.split("'TRL nozzle sleeve'").join("'NVX nozzle sleeve'");
content = content.split("'TRL/BOF/2026/018'").join("'NVX/BOF/2026/018'");
content = content.split("'Refractory/BOF/2026/018'").join("'NVX/BOF/2026/018'");

// 5. Labels & texts
content = content.split("'TRL input'").join("'Novafrax input'");
content = content.split("TRL input").join("Novafrax input");
content = content.split("B. TRL inputs").join("B. Novafrax inputs");
content = content.split("Common TRL inputs").join("Common Novafrax inputs");
content = content.split("Thickness rules (TRL)").join("Thickness rules (Novafrax)");
content = content.split("Thermal inputs (TRL)").join("Thermal inputs (Novafrax)");
content = content.split("TRL design basis").join("Novafrax design basis");
content = content.split("TRL convention").join("Novafrax convention");
content = content.split("Taphole (TRL)").join("Taphole (Novafrax)");
content = content.split("TRL values").join("Novafrax values");
content = content.split("until TRL confirms").join("until Novafrax confirms");
content = content.split("TRL minimum").join("Novafrax minimum");
content = content.split("TRL range").join("Novafrax range");
content = content.split("TRL grade").join("Novafrax grade");
content = content.split("why TRL deviates").join("why Novafrax deviates");
content = content.split("to confirm with TRL").join("to confirm with Novafrax");
content = content.split("wear rates TRL enters").join("wear rates Novafrax enters");
content = content.split("the TRL and customer values").join("the Novafrax and customer values");
content = content.split("common TRL and customer inputs").join("common Novafrax and customer inputs");
content = content.split("overrides the TRL grade").join("overrides the Novafrax grade");
content = content.split("Overrides TRL minimum").join("Overrides Novafrax minimum");

// 6. Checks replacements
content = content.replace("grade.replace('TRL HMR ', '')", "grade.replace(/^(TRL|NVX)\\\\s+HMR\\\\s+/, '')");
content = content.replace("z.grade.replace('TRL HMR ', '')", "z.grade.replace(/^(TRL|NVX)\\\\s+HMR\\\\s+/, '')");

// 7. Session app name & keys
content = content.replace(
    "const SESS_APP = 'TRL-BOF-Lining-Copilot', SESS_KEY = 'trlBofSession';",
    "const SESS_APP = 'Novafrax-BOF-Lining-Copilot', SESS_KEY = 'novafraxBofSession';"
);
content = content.replace(
    "const sessFileName = () => `TRL_${refSlug()}_session.json`;",
    "const sessFileName = () => `NVX_${refSlug()}_session.json`;"
);
content = content.replace(
    "const sessStepFileName = () => `${SESS.base || 'TRL_' + refSlug()}_step",
    "const sessStepFileName = () => `${SESS.base || 'NVX_' + refSlug()}_step"
);
content = content.replace(
    "let BOOT_LOCAL = null; try { BOOT_LOCAL = JSON.parse(localStorage.getItem(SESS_KEY) || 'null'); } catch (e) { BOOT_LOCAL = null; }",
    "let BOOT_LOCAL = null; try { BOOT_LOCAL = JSON.parse(localStorage.getItem(SESS_KEY) || localStorage.getItem('trlBofSession') || 'null'); } catch (e) { BOOT_LOCAL = null; }"
);
content = content.replace(
    "if (!o || (o.app !== SESS_APP && o.app !== 'Refractory-BOF-Lining-Copilot') || !o.state) {",
    "if (!o || (o.app !== SESS_APP && o.app !== 'TRL-BOF-Lining-Copilot' && o.app !== 'Refractory-BOF-Lining-Copilot') || !o.state) {"
);

fs.writeFileSync('ui/app.html', content, 'utf8');
console.log('ui/app.html updated successfully with Node.js');
