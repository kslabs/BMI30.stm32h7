"""Keep emergency USB support in every subsequent stock-built release."""
MARKER='# BMI30_PORTABLE_EMERGENCY_USB_V2'
BLOCK='''    # BMI30_PORTABLE_EMERGENCY_USB_V2
    local emergency_source="$engine_dir/bmi30_usb_recovery"
    if [[ ! -f "$emergency_source/bundle_feature_cli.py" ]]; then
        emergency_source="$PROJECT_ROOT/host/bmi30_active_runtime/project/host/bmi30_usb_recovery"
    fi
    [[ -f "$emergency_source/bundle_feature_cli.py" ]] || die "Emergency USB feature is required for new releases"
    python3 "$emergency_source/bundle_feature_cli.py" "$tmp_dir" "$BUNDLE_ID"

'''
def patch(source):
    if MARKER in source:return source
    anchor='    write_release_manifest "$tmp_dir" "$core_name" "$engine_name" "$gui_name" "$created_at"\n'
    if source.count(anchor)!=1:raise ValueError('Unsupported stock bundle builder; no modification')
    source=source.replace(anchor,BLOCK+anchor,1)
    anchor="        printf 'BMI30_BUNDLE_FORMAT=1\\n'\n"
    if source.count(anchor)!=1:raise ValueError('Unsupported bundle manifest')
    return source.replace(anchor,anchor+"        printf 'BMI30_BUNDLE_USB_EMERGENCY=2\\n'\n",1)
