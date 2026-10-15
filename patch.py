import io
import re

with io.open('ui/index.html', 'r', encoding='utf-8') as f:
    content = f.read()

# Replace the error box
pattern1 = r'(\s*)\$\{m\.errs\.length \? `<div class="note bad"><b>Cannot generate yet \– \$\{m\.errs\.length\} input\$\{m\.errs\.length > 1 \? \'s\' : \'\'\} missing or invalid:</b><ul class="small">\$\{m\.errs\.map\(e => `<li>\$\{esc\(e\)\}</li>`\)\.join\(\'\'\)\}</ul></div>` : \'\'\}'
replacement1 = r'\1<div id="linErrBox" style="display:${m.errs.length ? \'block\' : \'none\'}">\1  <div class="note bad"><b>Cannot generate yet – <span id="linErrC">${m.errs.length}</span> input${m.errs.length > 1 ? \'s\' : \'\'} missing or invalid:</b><ul class="small" id="linErrL">${m.errs.map(e => `<li>${esc(e)}</li>`).join(\'\')}</ul></div>\1</div>'
content = re.sub(pattern1, replacement1, content)

# Replace the gatebox
pattern2 = r'(\s*)<div class="gatebox" style="display:flex;margin-top:10px"><label class="chk" data-act="linConfirm" style="cursor:pointer"><input type="checkbox" \$\{conf \? \'checked\' : \'\'\} \$\{m\.errs\.length \? \'disabled\' : \'\'\}> 👤 I have checked these input values</label>\s*<button class="btn primary" data-act="linGenerate" \$\{conf && !L\.busy && !m\.errs\.length \? \'\' : \'disabled\'\}>\$\{L\.busy \? \'⏳ Generating…\' : \'⚙ Generate drawings & BOM\'\}</button></div>'
replacement2 = r'\1<div class="gatebox" id="linGateBox" style="display:flex;margin-top:10px"><label class="chk" data-act="linConfirm" style="cursor:pointer"><input type="checkbox" ${conf ? \'checked\' : \'\'} ${m.errs.length ? \'disabled\' : \'\'}> 👤 I have checked these input values</label>\1 <div style="flex:1"></div><button class="btn primary" data-act="linGenerate" ${conf && !L.busy && !m.errs.length ? \'\' : \'disabled\'}>${L.busy ? \'⏳ Generating…\' : \'⚙ Generate drawings & BOM\'}</button></div>'
content = re.sub(pattern2, replacement2, content)

with io.open('ui/index.html', 'w', encoding='utf-8') as f:
    f.write(content)
print("Done!")
