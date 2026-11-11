"""Optional host validation test: native doctor and catalog checks with saved evidence."""

import argparse
import json
import subprocess
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--hermes-root', type=Path, required=True)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    sys.path.insert(0, str(args.hermes_root.resolve()))
    from hermes_cli.plugin_dev import doctor_plugin
    from hermes_cli.plugin_validate import validate_plugin_dir
    root = Path(__file__).resolve().parents[1]
    doctor = doctor_plugin(root)
    validation = validate_plugin_dir(root)
    report = {'hermes_commit': subprocess.check_output(['git', '-C', str(args.hermes_root), 'rev-parse', 'HEAD'], text=True).strip(),
              'python': sys.version.split()[0], 'doctor_ok': doctor.ok, 'doctor': doctor.format_text(),
              'catalog_validation': validation.to_dict()}
    rendered = json.dumps(report, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + '\n', encoding='utf-8')
    print(rendered)
    return 0 if doctor.ok and validation.ok else 1


if __name__ == '__main__':
    raise SystemExit(main())
