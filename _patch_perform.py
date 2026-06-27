path = r'C:\Users\u729184\Desktop\_app_\recorder\replay.py'
with open(path) as f:
    src = f.read()

changes = [
    # click — add return after el.click() inside try
    (
        '        try:\n            el.click()\n        except ElementClickInterceptedException:',
        '        try:\n            el.click()\n            return f"clicked {_loc_label(strat, rank)}"\n        except ElementClickInterceptedException:',
    ),
    # dblclick — add return after double_click inside try
    (
        '            ActionChains(driver).double_click(el).perform()\n        except _bad_state:',
        '            ActionChains(driver).double_click(el).perform()\n            return f"double-clicked {_loc_label(strat, rank)}"\n        except _bad_state:',
    ),
    # contextmenu — add return after context_click inside try
    (
        '            ActionChains(driver).context_click(el).perform()\n        except _bad_state:',
        '            ActionChains(driver).context_click(el).perform()\n            return f"right-clicked {_loc_label(strat, rank)}"\n        except _bad_state:',
    ),
    # input/change — add return after send_keys inside try
    (
        '            el.clear()\n            el.send_keys(value)\n        except _bad_state:',
        '            el.clear()\n            el.send_keys(value)\n            return f"typed: {value!r} {_loc_label(strat, rank)}"\n        except _bad_state:',
    ),
    # keydown — add return after the if/elif/else block inside try
    (
        '            else:\n                el.send_keys(key)\n        except _bad_state:',
        '            else:\n                el.send_keys(key)\n            return f"keydown: {key} {_loc_label(strat, rank)}"\n        except _bad_state:',
    ),
]

for old, new in changes:
    count = src.count(old)
    src = src.replace(old, new)
    print(f"Replaced {count}x: {old[:60]!r}")

import ast
ast.parse(src)
print("Parse OK")

with open(path, 'w') as f:
    f.write(src)
print("Written.")
