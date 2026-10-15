import io
import re

with io.open('ui/index.html', 'r', encoding='utf-8') as f:
    content = f.read()

content = content.replace("\\'", "'")

with io.open('ui/index.html', 'w', encoding='utf-8') as f:
    f.write(content)
print("Fixed slashes!")
