"""Quick debug script to check the history page collapse IDs."""
import os, re, sys
os.environ['DJANGO_SETTINGS_MODULE'] = 'webapp.settings'
import django
django.setup()

from django.contrib.auth.models import User
from django.test import Client

# Reset admin password for test
u = User.objects.get(username='admin')
u.set_password('testpass123')
u.save()

c = Client()
ok = c.login(username='admin', password='testpass123')
print(f"login: {ok}")

for page_url, page_name in [('/history/', 'History'), ('/projects/', 'Sessions')]:
    print(f"\n{'='*60}")
    print(f"Page: {page_name} ({page_url})")
    print(f"{'='*60}")
    resp = c.get(page_url)
    print(f"status: {resp.status_code}")

    if resp.status_code == 200:
        content = resp.content.decode()

        # Check collapse IDs match between triggers and targets
        collapse_ids = re.findall(r'id="(folder-body-[^"]+)"', content)
        trigger_targets = re.findall(r'data-bs-target="#(folder-body-[^"]+)"', content)
        print(f"collapse IDs on page: {collapse_ids}")
        print(f"trigger targets:      {trigger_targets}")
        print(f"IDs match targets: {collapse_ids == trigger_targets}")

        # Check for duplicate IDs
        all_ids = re.findall(r' id="([^"]+)"', content)
        seen = set()
        dupes = []
        for i in all_ids:
            if i in seen:
                dupes.append(i)
            seen.add(i)
        if dupes:
            print(f"DUPLICATE IDs found: {dupes}")
        else:
            print("No duplicate IDs found.")

        # Check aria-expanded values
        aria_states = re.findall(r'class="[^"]*folder-tree-toggle[^"]*"[^>]*aria-expanded="([^"]+)"', content)
        print(f"aria-expanded states: {aria_states}")

        # Check if there are nested collapses
        folder_collapses = len(re.findall(r'class="collapse folder-collapse"', content))
        run_detail_collapses = len(re.findall(r'id="run-detail-', content))
        print(f"folder collapses: {folder_collapses}")
        print(f"run detail collapses: {run_detail_collapses}")

        # Check folder-tree-caret usage
        caret_spans = re.findall(r'<span class="folder-tree-caret[^"]*"[^>]*>', content)
        print(f"caret spans: {len(caret_spans)}")
        for i, s in enumerate(caret_spans[:3]):
            print(f"  caret {i}: {s}")

        # Check buttons with folder-tree-toggle
        toggle_btns = re.findall(r'<button class="[^"]*folder-tree-toggle[^"]*"[^>]*>', content)
        print(f"toggle buttons: {len(toggle_btns)}")
        for i, b in enumerate(toggle_btns[:3]):
            print(f"  btn {i}: {b[:200]}")

        # Show folder names
        folder_names = re.findall(r'data-folder-name="([^"]*)"', content)
        print(f"folder names: {folder_names}")
    elif resp.status_code == 302:
        print(f"Redirected to: {resp.url}")
