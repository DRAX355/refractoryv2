import io
import re

with io.open('ui/index.html', 'r', encoding='utf-8') as f:
    content = f.read()

# Replace all occurrences of word's inside single quotes.
# We want to replace things like `pair's` with `pair\'s`
# Specifically, we know from the errors:
content = content.replace("pair's", r"pair\'s")
content = content.replace("customer's", r"customer\'s")
content = content.replace("person 1's", r"person 1\'s")

with io.open('ui/index.html', 'w', encoding='utf-8') as f:
    f.write(content)
print("Replaced!")
