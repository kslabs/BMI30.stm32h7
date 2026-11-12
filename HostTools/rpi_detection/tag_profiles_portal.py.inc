# Tag-specific detector profiles; embedded in the portal, outside acquisition.
TAG_PROFILE_KEYS = tuple(dict.fromkeys((
    'confirm0', 'confirm1', 'threshold0', 'threshold1', 'auto0', 'auto1',
    *CORE_TAG_DETECTION_KEYS,
)))
TAG_PROFILES_PATH = pathlib.Path(CONFIG_JSON).with_name('bmi30_tag_profiles.json')
_TAG_PROFILES_LOCK = threading.RLock()


def _tag_profile_name(value):
    name = str(value or '').strip()
    if not 1 <= len(name) <= 64 or any(ord(c) < 32 or ord(c) == 127 for c in name):
        raise ValueError('Enter a profile name of 1 to 64 characters.')
    return name


def _tag_profile_settings(cfg):
    normalized = _normalize_tag_detection_config(cfg)
    return {key: normalized[key] for key in TAG_PROFILE_KEYS}


def _tag_profiles_load():
    try:
        raw = TAG_PROFILES_PATH.read_text(encoding='utf-8')
    except FileNotFoundError:
        return {'format': 'bmi30-tag-profiles-v1', 'profiles': {}}
    data = json.loads(raw)
    if not isinstance(data, dict) or data.get('format') != 'bmi30-tag-profiles-v1':
        raise ValueError('Unsupported tag profile file; existing profiles were preserved.')
    profiles = data.get('profiles')
    if not isinstance(profiles, dict) or len(profiles) > 32:
        raise ValueError('Invalid tag profile collection.')
    for name, item in profiles.items():
        if _tag_profile_name(name) != name or not isinstance(item, dict):
            raise ValueError('Invalid tag profile entry.')
        settings = item.get('settings')
        if not isinstance(settings, dict) or set(settings) != set(TAG_PROFILE_KEYS):
            raise ValueError('Incomplete tag profile; existing data was preserved.')
        # Reject invalid or silently clamped values instead of changing a saved profile.
        if _tag_profile_settings(settings) != settings:
            raise ValueError('Invalid tag profile settings.')
    return data


def _tag_profiles_revision(data):
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False,
                                     allow_nan=False).encode('utf-8')).hexdigest()


def _tag_profiles_write(data):
    import tempfile
    TAG_PROFILES_PATH.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + '\n'
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8',
                dir=TAG_PROFILES_PATH.parent, prefix='.tag-profiles-', delete=False) as handle:
            temporary = pathlib.Path(handle.name)
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o644)
        os.replace(temporary, TAG_PROFILES_PATH)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def _tag_profile_live_config():
    # An unavailable/stale core must never be mistaken for default detector settings.
    with urlopen(f'{CORE_SERVICE_URL}/api/status', timeout=2) as response:
        status = json.loads(response.read().decode('utf-8'))
    if (not status.get('ok') or status.get('busy') or status.get('stale')
            or not status.get('connection', {}).get('connected')):
        raise ValueError('The detector is unavailable; no profile was changed.')
    live = load_tag_detection_config_from_core()
    if live is None:
        raise ValueError('Unable to read active detector settings.')
    # Preserve UI-only fields from the saved configuration, not normalized defaults.
    cfg = load_tag_detection_config()
    cfg.update(_tag_profile_settings(live))
    cfg.update({key: live[key] for key in ('enabled0', 'enabled1')})
    return _normalize_tag_detection_config(cfg)


def _tag_profile_runtime_apply(settings):
    request = Request(f'{CORE_SERVICE_URL}/api/command',
        data=json.dumps({'action': 'runtime_detector_settings', 'params': settings}).encode('utf-8'),
        headers={'Content-Type': 'application/json', 'X-BMI30-Source': 'portal_tag_profile'},
        method='POST')
    with urlopen(request, timeout=5) as response:
        result = json.loads(response.read().decode('utf-8'))
    if not result.get('ok') or result.get('persisted') is not False:
        raise ValueError(str(result.get('error') or 'Detector rejected the profile.'))


def tag_profile_operation(form):
    with _TAG_PROFILES_LOCK:
        data = _tag_profiles_load()
        if form.get('profiles_revision') != _tag_profiles_revision(data):
            raise ValueError('Profiles changed in another window. Reload this page and retry.')
        operation = str(form.get('operation', ''))
        name = _tag_profile_name(form.get('profile_name') if operation == 'save'
                                 else form.get('profile_selected'))
        live = _tag_profile_live_config()
        if operation == 'save':
            if name not in data['profiles'] and len(data['profiles']) >= 32:
                raise ValueError('At most 32 tag profiles can be saved.')
            now = dt.datetime.now(dt.timezone.utc).isoformat()
            old = data['profiles'].get(name, {})
            data['profiles'][name] = {
                'settings': _tag_profile_settings(live),
                'created_at': old.get('created_at', now), 'updated_at': now,
                'source_device': socket.gethostname(),
            }
            _tag_profiles_write(data)
            return f'Profile "{name}" saved from the active detector.'
        if operation != 'apply':
            raise ValueError('Unknown profile operation.')
        if name not in data['profiles']:
            raise ValueError('This tag profile no longer exists.')
        target = dict(data['profiles'][name]['settings'])
        available = {item['filename'] for item in list_tag_response_filter_files()}
        for key in ('tag_filter_slot1', 'tag_filter_slot2', 'tag_filter_slot3'):
            if target[key] and target[key] not in available:
                raise ValueError(f'Response filter "{target[key]}" is unavailable on this device.')
        original = _tag_profile_settings(live)
        # Write only detector keys to runtime; enabled channels, optics and outputs stay local.
        try:
            _tag_profile_runtime_apply(target)
            applied = _tag_profile_live_config()
            if _tag_profile_settings(applied) != target:
                raise ValueError('Detector profile readback differs from the requested settings.')
            save_tag_detection_config(applied)
        except Exception as error:
            try:
                _tag_profile_runtime_apply(original)
            except Exception as restore_error:
                raise ValueError(f'Profile not confirmed: {error}. Restore failed: {restore_error}') from error
            raise ValueError(f'Profile not applied; previous runtime settings restored: {error}') from error
        return f'Profile "{name}" applied.'


def render_tag_profile_controls(cfg):
    try:
        with _TAG_PROFILES_LOCK:
            data = _tag_profiles_load()
        profiles = data['profiles']
        current = _tag_profile_settings(cfg)
        matching = [name for name, item in profiles.items() if item['settings'] == current]
        selected = matching[0] if matching else ''
        options = '<option value="">Select a saved profile</option>'
        for name in sorted(profiles, key=str.casefold):
            escaped = html.escape(name, quote=True)
            chosen = ' selected' if name == selected else ''
            options += f'<option value="{escaped}"{chosen}>{escaped}</option>'
        status = f'Settings match: {selected}' if selected else 'Current settings do not match a saved profile.'
        return (
            '<div class="section" data-tag-profile-controls><h2>Tag Profiles</h2>'
            '<p class="hint">Save active detector settings for each tag type. '
            'Apply any edits below before saving a profile.</p>'
            '<form class="config-form" method="post" action="portal-tag-profile">'
            f'<input type="hidden" name="profiles_revision" value="{_tag_profiles_revision(data)}">'
            '<div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">'
            f'<label>Profile <select name="profile_selected" aria-label="Tag profile">{options}</select></label>'
            '<button type="submit" name="operation" value="apply">Apply profile</button>'
            f'<label>Name <input name="profile_name" maxlength="64" value="{html.escape(selected, quote=True)}" '
            'placeholder="Casino" aria-label="Profile name"></label>'
            '<button type="submit" name="operation" value="save">Save active settings</button>'
            f'</div><p class="hint">{html.escape(status)}</p></form></div>'
        )
    except Exception as error:
        return '<div class="section"><h2>Tag Profiles</h2><p>' + html.escape(str(error)) + '</p></div>'
