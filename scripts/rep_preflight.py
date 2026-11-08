"""Offline XML validation. Never calls PAC. No signature/custody claims."""
import argparse
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.services.rep_validation import validate


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('xml',type=Path)
    parser.add_argument('--signed',action='store_true',help='Require original CFDI/TFD schema fields; does not verify signatures')
    args=parser.parse_args()
    report=validate(args.xml.read_text(),signed=args.signed)
    print(json.dumps(report,indent=2))
    return 0 if report['valid'] else 1


if __name__=='__main__':sys.exit(main())
