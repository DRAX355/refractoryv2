import io
p = r"d:\ZeroZeta\refractory-master (2)\refractory-master\ui\app.html"
t = io.open(p, encoding="utf-8", newline="").read()
old = '    <button class="tool" data-act="audit">🕘 Audit log</button>\r\n  </aside>'
assert old in t
new = '''    <button class="tool" data-act="audit">🕘 Audit log</button>
    <button class="tool" onclick="openControlCenter('Formulas')" title="View and edit the calculation formulas">ƒx Formulas</button>
    <button class="tool" onclick="openControlCenter('Properties')" title="Locked chemical properties and grades">🔒 Properties</button>
    <button class="tool" onclick="openControlCenter('Pre-populate')" title="Reuse recent / popular settings">⭐ Saved settings</button>
    <button class="tool" onclick="openControlCenter('Audit log')" title="Tamper-evident change history">📜 Change history</button>
  </aside>
  <div id="ccov" style="display:none;position:fixed;inset:0;z-index:9999;background:rgba(0,0,0,.45)">
    <div style="position:absolute;inset:3% 4%;background:#fff;border-radius:10px;overflow:hidden;display:flex;flex-direction:column;box-shadow:0 10px 40px rgba(0,0,0,.4)">
      <button onclick="closeControlCenter()" style="align-self:flex-end;margin:6px 10px;border:0;background:none;font-size:20px;cursor:pointer" aria-label="Close">✕</button>
      <iframe id="ccframe" style="flex:1;border:0;width:100%" title="Control Center"></iframe>
    </div>
  </div>
  <script>
    function openControlCenter(tab) {
      var f = document.getElementById('ccframe'), url = 'control_center.html#' + encodeURIComponent(tab);
      if (f.getAttribute('src') && f.getAttribute('src').split('#')[0] === 'control_center.html') f.contentWindow.location.hash = encodeURIComponent(tab);
      else f.src = url;
      document.getElementById('ccov').style.display = 'block';
    }
    function closeControlCenter() { document.getElementById('ccov').style.display = 'none'; }
    document.addEventListener('keydown', function (e) { if (e.key === 'Escape') closeControlCenter(); });
  </script>'''.replace("\n", "\r\n")
io.open(p, "w", encoding="utf-8", newline="").write(t.replace(old, new))
print("ok")
