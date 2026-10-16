"""Optional: build certs/ca_bundle.pem = certifi roots + an extra root CA (e.g. Avast's).

Only needed if requests fail with CERTIFICATE_VERIFY_FAILED because antivirus
re-signs HTTPS traffic. Usage:
    .venv\\Scripts\\python.exe make_ca_bundle.py "C:\\ProgramData\\Avast Software\\Avast\\wscert.pem"
"""
import sys
from pathlib import Path

import certifi

import config

extra_ca = Path(sys.argv[1])
out = config.PROJECT_DIR / "certs" / "ca_bundle.pem"
out.parent.mkdir(exist_ok=True)
out.write_text(Path(certifi.where()).read_text() + "\n" + extra_ca.read_text())
print(f"wrote {out}")
