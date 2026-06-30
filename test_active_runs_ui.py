#!/usr/bin/env python3
"""
Test script to verify Active Runs Monitor UI buttons are visible.
This script checks that the control buttons (pause, stop, watch run, clear) render correctly.
"""

import re
from pathlib import Path

def test_button_rendering():
    """Verify button HTML generation in renderRun function"""
    template_file = Path('recorder/templates/recorder/sessions.html')
    
    if not template_file.exists():
        print(f"❌ Template file not found: {template_file}")
        return False
    
    content = template_file.read_text()
    
    # Check for button CSS changes
    checks = {
        "overflow: visible on card": 'overflow: visible;' in content and '.active-run-card' in content,
        "overflow-x: auto on header": 'overflow-x: auto;' in content and '.active-run-card-hdr' in content,
        "Pause button generation": 'ar-pause-btn' in content,
        "Stop button generation": 'ar-stop-btn' in content,
        "Clear button generation": 'ar-clear-btn' in content,
        "Watch run button generation": 'bi-arrow-up-right-square' in content,
        "Button classes for styling": 'arp-card-btn-secondary' in content and 'arp-card-btn-danger' in content and 'arp-card-btn-primary' in content,
        "Event handlers attached": 'ar-pause-btn' in content and 'addEventListener' in content,
    }
    
    all_passed = True
    for check_name, check_result in checks.items():
        status = "✅" if check_result else "❌"
        print(f"{status} {check_name}")
        if not check_result:
            all_passed = False
    
    return all_passed

def test_css_layout():
    """Verify CSS layout allows buttons to be visible"""
    template_file = Path('recorder/templates/recorder/sessions.html')
    content = template_file.read_text()
    
    # Extract card header CSS
    hdr_pattern = r'\.active-run-card-hdr\s*\{([^}]*)\}'
    hdr_match = re.search(hdr_pattern, content, re.DOTALL)
    
    if hdr_match:
        hdr_css = hdr_match.group(1)
        print("\n📋 Active Run Card Header CSS:")
        # Print key properties
        for line in hdr_css.split(';'):
            line = line.strip()
            if line and any(prop in line for prop in ['display', 'flex', 'gap', 'overflow', 'padding']):
                print(f"   {line};")
        
        # Verify key properties
        required_props = {
            'display: flex': 'display: flex' in hdr_css,
            'gap/spacing': 'gap' in hdr_css,
            'overflow handling': 'overflow' in hdr_css,
        }
        
        print("\n📊 Layout Requirements:")
        all_good = True
        for prop_name, has_prop in required_props.items():
            status = "✅" if has_prop else "❌"
            print(f"{status} {prop_name}")
            if not has_prop:
                all_good = False
        
        return all_good
    
    print("❌ Could not find active-run-card-hdr CSS")
    return False

if __name__ == '__main__':
    print("🔍 Testing Active Runs Monitor UI Button Visibility\n")
    print("=" * 60)
    
    print("\n1️⃣  Button Rendering Checks:")
    print("-" * 60)
    rendering_ok = test_button_rendering()
    
    print("\n2️⃣  CSS Layout Checks:")
    print("-" * 60)
    layout_ok = test_css_layout()
    
    print("\n" + "=" * 60)
    if rendering_ok and layout_ok:
        print("✅ All UI tests passed! Buttons should be visible.")
    else:
        print("❌ Some tests failed. Buttons may not display correctly.")
