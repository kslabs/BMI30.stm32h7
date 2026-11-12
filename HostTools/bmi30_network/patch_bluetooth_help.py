"""Describe Bluetooth audio and compatible test controllers beside the title."""
import ast
import html

HELP = ('Connect Bluetooth headsets or microphones for voice annotations and audio '
        'feedback while testing TAG labels. Bluetooth buttons/controllers can '
        'trigger test actions when their HID profile and key assignments are supported.')
OLD = '<h2>Bluetooth Headphones and Headsets</h2>'
NEW = '<h2>Bluetooth Headphones and Headsets <b class="help tag-help" tabindex="0" role="button" aria-label="' + html.escape(HELP, quote=True) + '" data-tip="' + html.escape(HELP, quote=True) + '">?</b></h2>'


def patch(source):
    if NEW not in source:
        if source.count(OLD) != 1:
            raise ValueError('Unexpected Bluetooth heading')
        source = source.replace(OLD, NEW, 1)
    ast.parse(source)
    return source
