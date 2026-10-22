#!/usr/bin/env bash
set -euo pipefail

# Requires an Ubuntu runner and an interpreter selected by actions/setup-python.
# pip trusts both the system store and its own certifi bundle.
sudo apt-get update -q
sudo apt-get install -y --only-upgrade ca-certificates
python3 -I - <<'PY'
import ssl

blocked = {"emSign ECC Root CA - G3", "AAA Certificate Services"}
trusted = {
    value
    for cert in ssl.create_default_context().get_ca_certs()
    for rdn in cert["subject"]
    for key, value in rdn
    if key == "commonName"
}
if trusted & blocked:
    raise SystemExit(f"System trust store still contains: {sorted(trusted & blocked)}")
PY

# The digest authenticates the bootstrap even when the old pip trusts bad CAs.
# https://discuss.python.org/t/109576
python3 -I -m pip --isolated --disable-pip-version-check install \
  --no-index --no-deps --no-cache-dir --force-reinstall \
  'pip @ https://files.pythonhosted.org/packages/3f/06/3e4420302363fbb71be3c7f35ce042f54bfca330e3e66b3a2e3c4cca579f/pip-26.2.2-py3-none-any.whl#sha256=fd7f4dfa8190e014013b9f08f3e973f4e4c26ffd179a204895533a7254813a70'

# requests and the Zulip client use this separate bundle.
python3 -I -m pip --isolated --disable-pip-version-check install --upgrade 'certifi>=2026.10.19'
