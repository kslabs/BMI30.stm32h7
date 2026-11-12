"""Remove the redundant third controls row from the 2026-09-29 websplit core."""
import ast
import pathlib
import sys


def remove_once(source, start, end):
    assert source.count(start) == 1, start
    first = source.index(start)
    last = source.index(end, first)
    return source[:first] + source[last:]


def patch_core(source):
    # Restore the existing two-row desktop/mobile CSS, including plot space.
    source = remove_once(source,
        '#scopeControls{grid-template-rows:repeat(3,var(--top-control-row-h,30px))!important}',
        '</style>')
    source = remove_once(source, '      <div class="review-tools">', '    </section>')
    source = remove_once(source, 'function changeAutoFreezeSource()', 'function autoFreezeNeedsFrame(')
    source = remove_once(source, 'function reviewReason(diag)', 'function setFreezeMode(')
    assert source.count('updateReviewDetails(f);') == 1
    source = source.replace('updateReviewDetails(f);', '')
    source = source.replace("setText('reviewSummary','The requested event expired; waiting for the next event.');", '')
    old = 'from detections or rejected candidates, selected beside this button;'
    assert source.count(old) == 1
    source = source.replace(old, 'from confirmed detections;')
    assert 'review-tools' not in source and 'id="autoFreezeSource"' not in source
    assert 'id="freezeBtn" onclick="toggleFreeze()"' in source
    assert 'function toggleFreeze()' in source
    ast.parse(source)
    return source


if __name__ == '__main__':
    before, after = map(pathlib.Path, sys.argv[1:])
    after.write_text(patch_core(before.read_text(encoding='utf-8')), encoding='utf-8', newline='\n')
