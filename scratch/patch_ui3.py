import io
p = r"d:\ZeroZeta\refractory-master (2)\refractory-master\ui\app.html"
t = io.open(p, encoding="utf-8", newline="").read()
t = t.replace("    .t-nvx,\r\n    .t-supplier {", "    .t-supplier {")
t = t.replace("['Supplier input', 'trl']", "['Supplier input', 'supplier']")
t = t.replace("const norm = String(g).replace(/^TRL\\s+/, 'NVX ');", "const norm = String(g);")
t = t.replace("`NVX_${refSlug()}_session.json`", "`LiningCopilot_${refSlug()}_session.json`").replace("'NVX_' + refSlug()", "'LiningCopilot_' + refSlug()")
# always-visible access: header button + floating button
hdr = '    <button class="tb" data-act="apiConfig" title="Configure Backend API URL">'
assert hdr in t
t = t.replace(hdr, '    <button class="tb" onclick="openControlCenter(\'Formulas\')" title="View and edit calculation formulas, locked properties, saved settings and change history">ƒx Formulas</button>\r\n' + hdr, 1)
fab = '<button id="ccfab" onclick="openControlCenter(\'Formulas\')" title="Edit formulas" style="position:fixed;right:18px;bottom:18px;z-index:9000;padding:10px 16px;border:0;border-radius:24px;background:#2f5fd0;color:#fff;font-weight:600;cursor:pointer;box-shadow:0 4px 14px rgba(0,0,0,.3)">ƒx Formulas</button>\r\n  <div id="ccov"'
t = t.replace('<div id="ccov"', fab, 1)
io.open(p, "w", encoding="utf-8", newline="").write(t)
print("done", "TRL" in t, "NVX" in t)
