import sys

path = r'C:\web__automation\recorder\templates\recorder\steps.html'
data = open(path, 'rb').read()
box = b'\xc3\xa2\xe2\x80\x9d\xe2\x82\xac'  # mojibake dash char

old = (
    b'  .cell-tag { display: none !important; }\r\n'
    b'\r\n'
    b'  /* ' + box + box + b' Raw event row ' + box + box + b' */\r\n'
    b'  .cell-raw {'
)

validation_css = (
    b'  /* Validation row */\r\n'
    b'  .cell-validation {\r\n'
    b'    order:11; flex: 0 0 100%;\r\n'
    b'    display: flex !important; align-items: center; gap: .4rem;\r\n'
    b'    border-bottom: 1px solid rgba(31,63,96,.06);\r\n'
    b'    padding: .3rem .6rem; font-size: .75rem;\r\n'
    b'    max-width: none !important;\r\n'
    b'  }\r\n'
    b'  .cell-validation::before {\r\n'
    b'    content: "Valid."; flex: 0 0 4.2rem; flex-shrink: 0;\r\n'
    b'    font-weight: 600; color: #60758b;\r\n'
    b'    font-size: .65rem; text-transform: uppercase; letter-spacing: .05em;\r\n'
    b'  }\r\n'
    b'\r\n'
)

new = (
    b'  .cell-tag { display: none !important; }\r\n'
    b'\r\n'
    + validation_css +
    b'  /* ' + box + box + b' Raw event row ' + box + box + b' */\r\n'
    b'  .cell-raw {'
)

if old in data:
    data = data.replace(old, new, 1)
    open(path, 'wb').write(data)
    print('OK')
else:
    print('NOT FOUND')
    sys.exit(1)

