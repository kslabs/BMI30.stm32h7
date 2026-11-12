"""Invoked by the standard builder before release hashes are calculated."""
import pathlib,sys
from integrate_bundle import add_to_bundle,validate_included
def include(stage,version):
    stage=pathlib.Path(stage);manifest={'BMI30_BUNDLE_PORTAL_REL':'project/hotspot_info_server.py'}
    files={manifest['BMI30_BUNDLE_PORTAL_REL']:(stage/manifest['BMI30_BUNDLE_PORTAL_REL']).read_bytes()}
    add_to_bundle(files,manifest,version);validate_included(files,manifest)
    for name,data in files.items():
        path=stage/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
    return len(files)
if __name__=='__main__':include(sys.argv[1],sys.argv[2])
