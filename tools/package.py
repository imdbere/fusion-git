"""Build dist/FusionGit-<version>.zip, ready to unzip into Fusion's AddIns folder."""

import json
import os
import zipfile

ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
ADDIN = os.path.join(ROOT, "FusionGit")
EXCLUDED_DIRS = {"__pycache__"}
EXCLUDED_FILES = {".DS_Store"}


def version():
    with open(os.path.join(ADDIN, "FusionGit.manifest"), encoding="utf-8") as f:
        return json.load(f)["version"]


def main():
    os.makedirs(os.path.join(ROOT, "dist"), exist_ok=True)
    target = os.path.join(ROOT, "dist", f"FusionGit-{version()}.zip")
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for folder, dirs, files in os.walk(ADDIN):
            dirs[:] = sorted(d for d in dirs if d not in EXCLUDED_DIRS)
            for name in sorted(files):
                if name in EXCLUDED_FILES or name.endswith(".pyc"):
                    continue
                path = os.path.join(folder, name)
                archive.write(path, os.path.relpath(path, ROOT))
    print(target)


if __name__ == "__main__":
    main()
