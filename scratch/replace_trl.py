import re
import codecs

def update_app_html():
    with open('ui/app.html', 'r', encoding='utf-8') as f:
        content = f.read()

    # 1. CSS
    content = content.replace('.t-trl {', '.t-nvx,\n    .t-trl {')

    # 2. getApiBase dynamic query param
    old_api = """    /* ============ API CONFIGURATION ============ */
    function getApiBase() {
      if (typeof window !== 'undefined' && window.REFRACTORY_API_BASE) {
        return window.REFRACTORY_API_BASE.replace(/\/+$/, '');
      }
      try {
        const saved = localStorage.getItem('refractory_api_base');
        if (saved) return saved.replace(/\/+$/, '');
      } catch (e) {}

      if (typeof window !== 'undefined' && window.location && window.location.protocol.startsWith('http')) {
        // If frontend is running on dev port 3000
        if (window.location.port === '3000') {
          const isLocal = window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1';
          return `${window.location.protocol}//${window.location.hostname}:${isLocal ? '8000' : '8030'}`;
        }
        // If frontend is served directly by FastAPI (e.g. http://<host>:8000/ui/) or reverse proxy
        return window.location.origin;
      }
      return 'http://localhost:8000';
    }"""

    new_api = """    /* ============ API CONFIGURATION (Dynamic, Zero Hardcoding) ============ */
    function getApiBase() {
      // 1. Explicit window override
      if (typeof window !== 'undefined' && window.REFRACTORY_API_BASE) {
        return window.REFRACTORY_API_BASE.replace(/\/+$/, '');
      }
      // 2. Query param ?api=https://...
      try {
        if (typeof window !== 'undefined' && window.location && window.location.search) {
          const qApi = new URLSearchParams(window.location.search).get('api');
          if (qApi) {
            localStorage.setItem('refractory_api_base', qApi);
            return qApi.replace(/\/+$/, '');
          }
        }
      } catch (e) {}
      // 3. Saved localStorage
      try {
        const saved = localStorage.getItem('refractory_api_base');
        if (saved) return saved.replace(/\/+$/, '');
      } catch (e) {}

      // 4. Inferred from current browser origin
      if (typeof window !== 'undefined' && window.location && window.location.protocol.startsWith('http')) {
        // If frontend is running on dev port 3000
        if (window.location.port === '3000') {
          const isLocal = window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1';
          return `${window.location.protocol}//${window.location.hostname}:${isLocal ? '8000' : '8030'}`;
        }
        // If frontend is served directly by FastAPI or reverse proxy
        return window.location.origin;
      }
      return 'http://localhost:8000';
    }"""

    content = content.replace(old_api, new_api)

    # 3. Constants and Grades
    content = content.replace(
        "const GRADE_LIST = ['TRL HMR C2R', 'TRL HMR C3R', 'TRL HMR C4', 'TRL HMR C5R', 'TRL MC-4', 'Steel-clad (SCB)'];",
        "const GRADE_LIST = ['NVX HMR C2R', 'NVX HMR C3R', 'NVX HMR C4', 'NVX HMR C5R', 'NVX MC-4', 'Steel-clad (SCB)'];"
    )

    content = content.replace(
        "const GCOL = { 'TRL HMR C2R': '#6cae3e', 'TRL HMR C3R': '#2f5fd0', 'TRL HMR C4': '#b8892c', 'TRL HMR C5R': '#f08c86', 'TRL MC-4': '#35b6e8', 'Steel-clad (SCB)': '#8a8f98' };",
        "const GCOL = { 'NVX HMR C2R': '#6cae3e', 'NVX HMR C3R': '#2f5fd0', 'NVX HMR C4': '#b8892c', 'NVX HMR C5R': '#f08c86', 'NVX MC-4': '#35b6e8', 'Steel-clad (SCB)': '#8a8f98' };"
    )

    content = content.replace(
        "function gcol(g) { if (GCOL[g]) return GCOL[g];",
        "function gcol(g) { const norm = String(g).replace(/^TRL\\s+/, 'NVX '); if (GCOL[norm]) return GCOL[norm]; if (GCOL[g]) return GCOL[g];"
    )

    # 4. EXT_DEF & Initial state
    content = content.replace("'TRL HMR C2R'", "'NVX HMR C2R'")
    content = content.replace("'TRL MC-4'", "'NVX MC-4'")
    content = content.replace("'TRL HMR C5R'", "'NVX HMR C5R'")
    content = content.replace("'TRL HMR C3R'", "'NVX HMR C3R'")
    content = content.replace("'TRL HMR C4'", "'NVX HMR C4'")
    content = content.replace("'TRL HCH111B'", "'NVX HCH111B'")
    content = content.replace("'TRL RAM M-95'", "'NVX RAM M-95'")
    content = content.replace("'TRL nozzle block'", "'NVX nozzle block'")
    content = content.replace("'TRL nozzle sleeve'", "'NVX nozzle sleeve'")
    content = content.replace("'TRL/BOF/2026/018'", "'NVX/BOF/2026/018'")
    content = content.replace("'Refractory/BOF/2026/018'", "'NVX/BOF/2026/018'")

    # 5. Labels & texts
    content = content.replace("'TRL input'", "'Novafrax input'")
    content = content.replace("TRL input", "Novafrax input")
    content = content.replace("B. TRL inputs", "B. Novafrax inputs")
    content = content.replace("Common TRL inputs", "Common Novafrax inputs")
    content = content.replace("Thickness rules (TRL)", "Thickness rules (Novafrax)")
    content = content.replace("Thermal inputs (TRL)", "Thermal inputs (Novafrax)")
    content = content.replace("TRL design basis", "Novafrax design basis")
    content = content.replace("TRL convention", "Novafrax convention")
    content = content.replace("Taphole (TRL)", "Taphole (Novafrax)")
    content = content.replace("TRL values", "Novafrax values")
    content = content.replace("until TRL confirms", "until Novafrax confirms")
    content = content.replace("TRL minimum", "Novafrax minimum")
    content = content.replace("TRL range", "Novafrax range")
    content = content.replace("TRL grade", "Novafrax grade")
    content = content.replace("why TRL deviates", "why Novafrax deviates")
    content = content.replace("to confirm with TRL", "to confirm with Novafrax")
    content = content.replace("wear rates TRL enters", "wear rates Novafrax enters")
    content = content.replace("the TRL and customer values", "the Novafrax and customer values")
    content = content.replace("common TRL and customer inputs", "common Novafrax and customer inputs")
    content = content.replace("overrides the TRL grade", "overrides the Novafrax grade")
    content = content.replace("Overrides TRL minimum", "Overrides Novafrax minimum")

    # 6. Checks replacements
    content = content.replace("grade.replace('TRL HMR ', '')", "grade.replace(/^(TRL|NVX)\\s+HMR\\s+/, '')")
    content = content.replace("z.grade.replace('TRL HMR ', '')", "z.grade.replace(/^(TRL|NVX)\\s+HMR\\s+/, '')")

    # 7. Session app name & keys
    content = content.replace(
        "const SESS_APP = 'TRL-BOF-Lining-Copilot', SESS_KEY = 'trlBofSession';",
        "const SESS_APP = 'Novafrax-BOF-Lining-Copilot', SESS_KEY = 'novafraxBofSession';"
    )
    content = content.replace(
        "const sessFileName = () => `TRL_${refSlug()}_session.json`;",
        "const sessFileName = () => `NVX_${refSlug()}_session.json`;"
    )
    content = content.replace(
        "const sessStepFileName = () => `${SESS.base || 'TRL_' + refSlug()}_step",
        "const sessStepFileName = () => `${SESS.base || 'NVX_' + refSlug()}_step"
    )
    content = content.replace(
        "let BOOT_LOCAL = null; try { BOOT_LOCAL = JSON.parse(localStorage.getItem(SESS_KEY) || 'null'); } catch (e) { BOOT_LOCAL = null; }",
        "let BOOT_LOCAL = null; try { BOOT_LOCAL = JSON.parse(localStorage.getItem(SESS_KEY) || localStorage.getItem('trlBofSession') || 'null'); } catch (e) { BOOT_LOCAL = null; }"
    )
    content = content.replace(
        "if (!o || (o.app !== SESS_APP && o.app !== 'Refractory-BOF-Lining-Copilot') || !o.state) {",
        "if (!o || (o.app !== SESS_APP && o.app !== 'TRL-BOF-Lining-Copilot' && o.app !== 'Refractory-BOF-Lining-Copilot') || !o.state) {"
    )

    with open('ui/app.html', 'w', encoding='utf-8') as f:
        f.write(content)

    print('ui/app.html updated successfully')

if __name__ == '__main__':
    update_app_html()
