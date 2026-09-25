#!/usr/bin/env bash
# Create a self-signed code-signing identity ("Aether Dev") in the login
# keychain, once. Signing every build with the same identity keeps macOS
# privacy grants (Accessibility, Screen Recording) across rebuilds; ad-hoc
# signatures change with every build and the grants are lost.
#
# No Apple Developer account needed. macOS asks for your login password once
# to trust the certificate for code signing.
#
# GUI alternative: Keychain Access → Certificate Assistant → Create a
# Certificate… → Name "Aether Dev", Identity Type "Self Signed Root",
# Certificate Type "Code Signing".
set -euo pipefail

NAME="${1:-Aether Dev}"
KEYCHAIN="$HOME/Library/Keychains/login.keychain-db"
OPENSSL=/usr/bin/openssl   # LibreSSL: its PKCS#12 output imports cleanly

if security find-identity -v -p codesigning | grep -q "\"$NAME\""; then
  echo "Identity '$NAME' already exists."
  exit 0
fi

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
cat > "$TMP/cert.cnf" <<CNF
[req]
distinguished_name = dn
x509_extensions = ext
prompt = no
[dn]
CN = $NAME
[ext]
basicConstraints = critical,CA:false
keyUsage = critical,digitalSignature
extendedKeyUsage = critical,codeSigning
CNF

"$OPENSSL" req -x509 -newkey rsa:2048 -nodes -days 3650 \
  -keyout "$TMP/key.pem" -out "$TMP/cert.pem" -config "$TMP/cert.cnf"
"$OPENSSL" pkcs12 -export -inkey "$TMP/key.pem" -in "$TMP/cert.pem" \
  -name "$NAME" -out "$TMP/id.p12" -passout pass:aether

security import "$TMP/id.p12" -k "$KEYCHAIN" -P aether -T /usr/bin/codesign
echo "Trusting the certificate for code signing (macOS will ask for your password)…"
security add-trusted-cert -p codeSign -k "$KEYCHAIN" "$TMP/cert.pem"

security find-identity -v -p codesigning | grep "\"$NAME\"" \
  && echo "Created identity '$NAME'. Build with: make app"
