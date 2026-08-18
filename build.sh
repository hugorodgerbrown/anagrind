#!/usr/bin/env bash
# Deploy build. Produces everything that is NOT in git:
#   .vocab-cache.pkl  the built index          (~19 MB)
#   anagrind.html     single-file build        (~2.2 MB)
#   dist/             the folder you host      (~2.2 MB)
set -euo pipefail

pip install -r requirements-build.txt
python -c "import nltk; nltk.download('wordnet', quiet=True)"

python vocab.py                                   # ~30s
python build_payload.py
python -c "
payload = open('payload.b64').read().strip()
open('anagrind.html','w').write(
    open('ui.template.html').read().replace('__PAYLOAD__', payload))
"
python build_dist.py
echo "build complete"
