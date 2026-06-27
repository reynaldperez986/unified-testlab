with open(r'c:\web__automation\recorder\templates\recorder\steps.html', 'r', encoding='utf-8') as f:
    content = f.read()

# ── Patch 1: add validation cell in _buildRow ──────────────────────────────
OLD1 = (
    "        dataHtml +\n"
    "        '<td class=\"cell-recorder small text-muted\" data-label=\"Recorder\">' + _esc(s.recorder || '\\u2014') + '</td>' +\n"
    "        '<td class=\"cell-runner small text-muted\" data-label=\"Script Runner\">' + _esc(s.runner || '\\u2014') + '</td>' +"
)
NEW1 = (
    "        dataHtml +\n"
    "        '<td class=\"cell-validation small\" data-label=\"Validation\"' +\n"
    "          ' data-step-no-val=\"' + s.step_no + '\"' +\n"
    "          ' data-cur-val=\"\"' +\n"
    "          ' style=\"min-width:120px;cursor:pointer;\">' +\n"
    "          '<div class=\"validation-view d-flex align-items-center gap-1\">' +\n"
    "          '<span class=\"text-muted validation-empty\" style=\"font-size:.75rem;\">&mdash;</span>' +\n"
    "          '<i class=\"bi bi-pencil text-muted ms-1\" style=\"font-size:.62rem;opacity:.4;flex-shrink:0;\"></i>' +\n"
    "          '</div>' +\n"
    "        '</td>' +\n"
    "        '<td class=\"cell-recorder small text-muted\" data-label=\"Recorder\">' + _esc(s.recorder || '\\u2014') + '</td>' +\n"
    "        '<td class=\"cell-runner small text-muted\" data-label=\"Script Runner\">' + _esc(s.runner || '\\u2014') + '</td>' +"
)

if OLD1 in content:
    content = content.replace(OLD1, NEW1, 1)
    print('Patch 1 applied')
else:
    print('Patch 1 NOT FOUND')
    idx = content.find("'<td class=\"cell-recorder small text-muted\" data-label=\"Recorder\">'")
    print('  recorder td found at:', idx)
    if idx >= 0:
        print(repr(content[max(0,idx-80):idx+10]))

# ── Patch 2: bind validation in _bindRowEvents ─────────────────────────────
OLD2 = "      /* editable data-entry cell */\n      tr.querySelectorAll('.cell-data-editable').forEach(_bindDataEdit);\n    }"
NEW2 = "      /* editable data-entry cell */\n      tr.querySelectorAll('.cell-data-editable').forEach(_bindDataEdit);\n      /* editable validation cell */\n      tr.querySelectorAll('.cell-validation').forEach(_bindValidationEdit);\n    }"

if OLD2 in content:
    content = content.replace(OLD2, NEW2, 1)
    print('Patch 2 applied')
else:
    print('Patch 2 NOT FOUND')

with open(r'c:\web__automation\recorder\templates\recorder\steps.html', 'w', encoding='utf-8') as f:
    f.write(content)
print('Done')
