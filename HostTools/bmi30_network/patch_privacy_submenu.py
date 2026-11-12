"""Group existing access settings into five server-rendered, accessible tabs."""
import ast
from pathlib import Path
import re
import sys

HERE = Path(__file__).resolve().parent
CHANNELS = ('portal', 'hotspot', 'wifi', 'ethernet', 'remote')
TITLES = ('Portal', 'HotSpot', 'Internet WiFi', 'Ethernet', 'RDP/VNC')

CSS = '''
    .privacy-submenu{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:8px;margin-bottom:16px}
    .privacy-tab{min-width:0;text-align:left;display:grid;align-content:start;gap:8px;padding:12px;border:1px solid var(--line);border-radius:8px;background:var(--note-bg);color:var(--text);font:inherit;cursor:pointer}
    .privacy-tab[aria-selected="true"]{border-color:var(--accent);background:var(--accent-soft);box-shadow:inset 0 -3px 0 var(--accent)}
    .privacy-tab:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
    .privacy-tab-title{display:flex;align-items:center;justify-content:space-between;gap:8px;font-weight:700;font-size:14px}
    .privacy-tab-title strong{color:var(--accent);font-size:20px}
    .privacy-tab-summary{display:block;color:var(--muted);font-size:12px;overflow-wrap:anywhere}
    .privacy-zone[hidden]{display:none!important}
    .privacy-zone-head{display:flex;align-items:center;justify-content:flex-start;gap:10px;flex-wrap:wrap;margin-bottom:12px}
    .privacy-zone-head h3{margin:0}
    .privacy-zone-head>.privacy-title-actions{margin-left:auto}
    .privacy-zone-head .channel-permission{width:auto;height:auto;gap:8px;font-size:13px}
    .privacy-zone .privacy-form:first-of-type{border-top:0;padding-top:0}
    .privacy-zone .privacy-form{margin:0}
    .privacy-zone .privacy-note{margin-bottom:14px}
    .privacy-access-actions{display:flex;flex-wrap:wrap;gap:10px;margin:12px 0}
    @media(max-width:760px){.privacy-submenu{display:flex;overflow-x:auto;padding:2px 2px 8px;scroll-snap-type:x proximity}.privacy-tab{flex:0 0 150px;scroll-snap-align:start}.privacy-title-row{flex-wrap:wrap}}
'''

def patch(source):
    if 'id="privacy-submenu"' in source:
        raise ValueError('Submenu already installed; inspect the current source')
    old = '''    wifi_options_parts = ['<option value="">Choose visible network...</option>']
    for item in networks:
        ssid_value = html.escape(item["ssid"], quote=True)
        marker = " (connected)" if item.get("in_use") == "yes" else ""
        label = html.escape(f'{item["ssid"]} - {item.get("signal", "0")}% - {item.get("security", "open")}{marker}')
        selected = " selected" if item["ssid"] == wifi_meta.get("ssid") else ""
        wifi_options_parts.append(f'<option value="{ssid_value}"{selected}>{label}</option>')'''
    new = '''    wifi_current_ssid = str(wifi_active.get("ssid") or "") if wifi_active.get("connected") else ""
    wifi_selected_ssid = wifi_current_ssid or str(wifi_meta.get("ssid") or "")
    wifi_options_parts = ['<option value="">Choose visible network...</option>']
    wifi_seen = set()
    for item in networks:
        if item["ssid"] in wifi_seen:
            continue
        wifi_seen.add(item["ssid"])
        ssid_value = html.escape(item["ssid"], quote=True)
        marker = " (connected)" if item["ssid"] == wifi_current_ssid else ""
        label = html.escape(f'{item["ssid"]} - {item.get("signal", "0")}% - {item.get("security", "open")}{marker}')
        selected = " selected" if item["ssid"] == wifi_selected_ssid else ""
        wifi_options_parts.append(f'<option value="{ssid_value}"{selected}>{label}</option>')
    if wifi_selected_ssid and wifi_selected_ssid not in wifi_seen:
        value = html.escape(wifi_selected_ssid, quote=True)
        wifi_options_parts.insert(1, f'<option value="{value}" selected>{value}</option>')
'''.rstrip()
    if source.count(old) != 1:
        raise ValueError('Unexpected visible network selection')
    source = source.replace(old,new)
    start = source.index('        <section class="portal-panel" id="panel-privacy">')
    end = source.index('        <div id="engineer-modal"', start)
    region = source[start:end]
    forms = dict(re.findall(r'(          <form class="privacy-form"[^>]*action="([^"]+)".*?          </form>\n)', region, re.S))
    forms = {action: text for text, action in forms.items()}
    actions = ('portal-account-config', 'portal-hotspot-config', 'portal-wifi-config', 'portal-remote-config')
    if set(forms) != set(actions):
        raise ValueError('Unexpected access forms')
    permissions = re.findall(r'(<form method="post" action="portal-channel-config".*?</form>)', region, re.S)
    if len(permissions) != 4:
        raise ValueError('Unexpected channel permissions')
    summary = ('{portal_username}', '{hotspot_ssid}', '{wifi_saved_ssid or "---"}', 'LAN', '{remote_username}')
    parts = ['        <section class="portal-panel" id="panel-privacy">',
             '          <div class="privacy-submenu" id="privacy-submenu" role="tablist" aria-label="Access and Security">']
    for channel, title, value in zip(CHANNELS, TITLES, summary):
        active = channel == 'portal'
        parts += [f'            <button type="button" class="privacy-tab" id="privacy-tab-{channel}" role="tab" data-privacy-tab="{channel}" aria-controls="privacy-zone-{channel}" aria-selected="{str(active).lower()}" tabindex="{0 if active else -1}">',
                  f'              <span class="privacy-tab-title"><span>{title}</span><strong data-connection-count="{channel}">{{privacy_counts["{channel}"]}}</strong></span>',
                  f'              <span class="privacy-tab-summary">{value}</span>', '            </button>']
    parts += ['          </div>', '          <div class="privacy-forms">']
    by_channel = dict(zip(('portal','hotspot','wifi','remote'),actions))
    permission_titles = ('Allow HotSpot access', 'Allow Internet Wi-Fi', 'Allow Ethernet access', 'Allow RDP/VNC remote desktop')
    for i, (channel, title) in enumerate(zip(CHANNELS, TITLES)):
        parts += [f'            <section class="privacy-zone" id="privacy-zone-{channel}" data-privacy-zone="{channel}" role="tabpanel" aria-labelledby="privacy-tab-{channel}" tabindex="0"' + (' hidden>' if i else '>'),
                  f'              <div class="privacy-zone-head"><h3>{title}</h3>']
        if i:
            permission = permissions[i-1]
            permission = permission.replace('<input type="checkbox"', '<label><input type="checkbox" aria-label="' + permission_titles[i-1] + '"', 1)
            permission = permission.replace('</form>', '</label></form>')
            parts.append(permission)
        parts.append('              </div>')
        if channel in by_channel:
            form = forms[by_channel[channel]]
            form = re.sub(r'                <h3>.*?</h3>\n', '', form, count=1)
            form = form.replace('href="portal#privacy"', 'href="portal#privacy" data-privacy-keep="wifi"')
            if channel == 'wifi':
                actions_html = re.search(r'<div class="privacy-title-actions">.*?</div>', form, re.S).group(0)
                actions_html = actions_html.replace('type="submit"', 'type="submit" form="privacy-form-wifi"')
                parts.insert(len(parts)-1, actions_html)
                form = re.sub(r'^              <div class="privacy-title-row">.*?^              </div>\n', '', form, count=1, flags=re.S | re.M)
                form = form.replace('<form class="privacy-form"', '<form id="privacy-form-wifi" class="privacy-form"', 1)
            parts.append(form.rstrip())
        else:
            parts += ['              <div class="privacy-form">',
                      '                <div class="metric"><span>IP Address</span><strong>{privacy_ethernet_ip}</strong></div>',
                      '              </div>']
        parts.append('            </section>')
    parts += ['          </div>', '          <noscript><style>.privacy-zone[hidden]{{display:block!important}}</style></noscript>',
              '        </section>\n']
    new_region = '\n'.join(parts)
    # New CSS is inside the existing Python f-string.
    css = CSS.replace('{','{{').replace('}','}}')
    source = source[:start] + new_region + source[end:]
    anchor = '    .privacy-status-grid{{'
    at = source.index(anchor)
    source = source[:at] + css + source[at:]
    anchor = '    privacy_counts = {'
    at = source.index(anchor)
    source = source[:at] + '    privacy_ethernet_ip = html.escape(", ".join(str(item["ip"]) for item in collect_ipv4_interfaces() if classify_interface(item["iface"], item["ip"]) == "ethernet") or "---")\n' + source[at:]
    tree = ast.parse(source)
    access_node = next(n for n in tree.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='BMI30_ACCESS_JS' for t in n.targets))
    lines = source.splitlines(keepends=True)
    scripts = '<script>\n' + (HERE/'privacy_submenu.js').read_text(encoding='utf-8') + '\n</script>\n<script>\n' + (HERE/'access_ui.js').read_text(encoding='utf-8') + '\n</script>'
    lines[access_node.lineno-1:access_node.end_lineno] = ['BMI30_ACCESS_JS = ' + repr(scripts) + '\n']
    source = ''.join(lines)
    password_js = '<script>\n' + (HERE/'password_visibility.js').read_text(encoding='utf-8') + '\n</script>'
    anchor = 'def localize_ui_html(body):\n    return body.replace("</head>", BMI30_I18N_BOOTSTRAP + "</head>", 1)'
    replacement = 'BMI30_PASSWORD_VISIBILITY_JS = ' + repr(password_js) + '\n\n' + anchor.replace('BMI30_I18N_BOOTSTRAP +', 'BMI30_I18N_BOOTSTRAP + BMI30_PASSWORD_VISIBILITY_JS +')
    if source.count(anchor) != 1:
        raise ValueError('Unexpected shared HTML localization hook')
    source = source.replace(anchor, replacement)
    ast.parse(source)
    return source

if __name__ == '__main__':
    source, target = map(Path,sys.argv[1:])
    target.write_text(patch(source.read_text(encoding='utf-8')),encoding='utf-8',newline='\n')
