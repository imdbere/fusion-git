"""Build dist/FusionGit-<version>.zip, ready to add to Fusion.

The version is written into the packaged manifest. CI passes the release tag
(`--version 1.2.0`); without it the manifest's own version is used.
"""

import argparse
import json
import os
import zipfile

ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
ADDIN = os.path.join(ROOT, "FusionGit")
MANIFEST = os.path.join(ADDIN, "FusionGit.manifest")
EXCLUDED_DIRS = {"__pycache__"}
EXCLUDED_FILES = {".DS_Store"}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", help="version to write into the packaged manifest")
    args = parser.parse_args()

    with open(MANIFEST, encoding="utf-8") as f:
        manifest = json.load(f)
    if args.version:
        manifest["version"] = args.version
    version = manifest["version"]

    os.makedirs(os.path.join(ROOT, "dist"), exist_ok=True)
    target = os.path.join(ROOT, "dist", f"FusionGit-{version}.zip")
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for folder, dirs, files in os.walk(ADDIN):
            dirs[:] = sorted(d for d in dirs if d not in EXCLUDED_DIRS)
            for name in sorted(files):
                if name in EXCLUDED_FILES or name.endswith(".pyc"):
                    continue
                path = os.path.join(folder, name)
                arcname = os.path.relpath(path, ROOT)
                if path == MANIFEST:
                    archive.writestr(arcname, json.dumps(manifest, indent="\t") + "\n")
                else:
                    archive.write(path, arcname)
    print(target)


if __name__ == "__main__":
    main()
