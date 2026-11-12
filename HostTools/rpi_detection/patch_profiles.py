"""Embed tag profiles in the active portal while keeping core/engine unchanged."""
import argparse
import ast
from pathlib import Path


def patch(source):
    if 'def tag_profile_operation(' in source:
        raise ValueError('Tag profiles are already present')
    snippet = Path(__file__).with_name('tag_profiles_portal.py.inc').read_text(encoding='utf-8')
    anchor = 'def apply_group_optic_to_core(\n'
    assert source.count(anchor) == 1
    source = source.replace(anchor, snippet + '\n\n' + anchor)
    anchor = '    tag_common_mode_html = render_common_mode_controls(tag_cfg)\n'
    assert source.count(anchor) == 1
    source = source.replace(anchor, anchor + '    tag_profiles_html = render_tag_profile_controls(tag_cfg)\n')
    anchor = '        <section class="portal-panel" id="panel-detection">\n'
    assert source.count(anchor) == 1
    source = source.replace(anchor, anchor + '          {tag_profiles_html}\n')
    anchor = '        if path == "/portal-tag-detection-config":\n'
    assert source.count(anchor) == 1
    handler = '''        if path == "/portal-tag-profile":
            session = self._get_portal_session()
            if session is None:
                self._send_redirect(self._absolute_url(with_rev("/login"), scheme=self._preferred_scheme()),
                                    status=HTTPStatus.SEE_OTHER)
                return
            try:
                notice = tag_profile_operation(self._read_post_form())
                notice_kind = "ok"
                code = HTTPStatus.OK
            except Exception as error:
                notice = str(error)
                notice_kind = "error"
                code = HTTPStatus.BAD_REQUEST
            payload = render_portal_page(
                socket.gethostname(), session_username=str(session.get("u", "")),
                session_role=str(session.get("r", "user")), notice=notice,
                notice_kind=notice_kind, request_host=self.headers.get("Host", ""))
            self.send_response(code)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return

'''
    source = source.replace(anchor, handler + anchor)
    ast.parse(source)
    return source


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('source', type=Path)
    parser.add_argument('destination', type=Path)
    args = parser.parse_args()
    args.destination.write_text(patch(args.source.read_text(encoding='utf-8')), encoding='utf-8')
