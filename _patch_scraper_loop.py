"""Patch script: replace save_snapshot upsert loop with stop/pause/progress support."""
with open('c:/web__automation/web_scraper.py', encoding='utf-8') as f:
    lines = f.readlines()

# Find '# Upsert current items' line (0-indexed)
upsert_start = next(i for i, l in enumerate(lines) if '# Upsert current items' in l)
# Find the closing ')' of cur.execute call — the line just before self.conn.commit()
upsert_end = next(i for i in range(upsert_start, len(lines)) if 'self.conn.commit()' in lines[i])

print(f'Replacing lines {upsert_start+1}..{upsert_end} (0-indexed {upsert_start}..{upsert_end-1})')

new_block = (
    '\t\t\t\t\t# Upsert current items\n'
    '\t\t\t\t\tfor idx_, (page_url_, page_name_, element_type_, fingerprint_, locator_property_) in enumerate(item_data):\n'
    '\t\t\t\t\t\t\tif stop_event and stop_event.is_set():\n'
    '\t\t\t\t\t\t\t\t\tbreak\n'
    '\t\t\t\t\t\t\tif pause_event:\n'
    '\t\t\t\t\t\t\t\t\tpause_event.wait()  # block while paused\n'
    '\t\t\t\t\t\t\tcur.execute(\n'
    '\t\t\t\t\t\t\t\t\t"""\n'
    '\t\t\t\t\t\t\t\t\tINSERT INTO ai_databank (\n'
    '\t\t\t\t\t\t\t\t\t\t\tpage_url, page_name, element_type, element_fingerprint,\n'
    '\t\t\t\t\t\t\t\t\t\t\tlocator_property, screenshot_png, updated_at\n'
    '\t\t\t\t\t\t\t\t\t)\n'
    '\t\t\t\t\t\t\t\t\tVALUES (%s, %s, %s, %s, %s, %s, NOW())\n'
    '\t\t\t\t\t\t\t\t\tON CONFLICT (page_url, element_fingerprint)\n'
    "\t\t\t\t\t\t\t\t\tWHERE element_fingerprint IS NOT NULL AND element_fingerprint <> ''\n"
    '\t\t\t\t\t\t\t\t\tDO UPDATE SET\n'
    '\t\t\t\t\t\t\t\t\t\t\tpage_name = EXCLUDED.page_name,\n'
    '\t\t\t\t\t\t\t\t\t\t\telement_type = EXCLUDED.element_type,\n'
    '\t\t\t\t\t\t\t\t\t\t\tlocator_property = EXCLUDED.locator_property,\n'
    '\t\t\t\t\t\t\t\t\t\t\tscreenshot_png = EXCLUDED.screenshot_png,\n'
    '\t\t\t\t\t\t\t\t\t\t\tupdated_at = NOW()\n'
    '\t\t\t\t\t\t\t\t\t""",\n'
    '\t\t\t\t\t\t\t\t\t[\n'
    '\t\t\t\t\t\t\t\t\t\t\tpage_url_,\n'
    '\t\t\t\t\t\t\t\t\t\t\tpage_name_,\n'
    '\t\t\t\t\t\t\t\t\t\t\telement_type_,\n'
    '\t\t\t\t\t\t\t\t\t\t\tfingerprint_,\n'
    '\t\t\t\t\t\t\t\t\t\t\tJson(locator_property_),\n'
    '\t\t\t\t\t\t\t\t\t\t\tpsycopg2.Binary(screenshot_png),\n'
    '\t\t\t\t\t\t\t\t\t],\n'
    '\t\t\t\t\t\t\t)\n'
    '\t\t\t\t\t\t\tif on_progress:\n'
    '\t\t\t\t\t\t\t\t\ton_progress(idx_ + 1, len(item_data))\n'
)

lines[upsert_start:upsert_end] = [new_block]

with open('c:/web__automation/web_scraper.py', 'w', encoding='utf-8') as f:
    f.writelines(lines)

import ast
ast.parse(open('c:/web__automation/web_scraper.py', encoding='utf-8').read())
print('Syntax OK. New total lines:', len(lines))
