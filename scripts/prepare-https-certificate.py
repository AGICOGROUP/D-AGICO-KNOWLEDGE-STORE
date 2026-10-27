"""Validate and import a public HTTPS certificate bundle into protected data storage."""
import argparse
import ctypes
import json
import os
from datetime import datetime, timezone
from pathlib import Path
import subprocess
import zipfile
from cryptography import x509
from cryptography.hazmat.primitives import serialization


def inspect_bundle(archive, hostname):
    keys = []
    certs = []
    with zipfile.ZipFile(archive) as bundle:
        if sum(i.file_size for i in bundle.infolist()) > 8 * 1024 * 1024:
            raise ValueError('Certificate archive exceeds 8 MB limit')
        for entry in bundle.infolist():
            if entry.is_dir():
                continue
            raw = bundle.read(entry)
            if b'PRIVATE KEY-----' in raw:
                try:
                    keys.append(serialization.load_pem_private_key(raw, password=None))
                except (ValueError, TypeError):
                    raise ValueError('Private key is encrypted or invalid; encrypted key requires a separate import procedure') from None
            if b'-----BEGIN CERTIFICATE-----' in raw:
                certs.extend(x509.load_pem_x509_certificates(raw))
    certs = list({c.fingerprint(__import__('cryptography').hazmat.primitives.hashes.SHA256()): c for c in certs}.values())
    def public(key):
        return key.public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    now = datetime.now(timezone.utc)
    for cert in certs:
        try:
            names = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value.get_values_for_type(x509.DNSName)
        except x509.ExtensionNotFound:
            continue
        match = any(n.lower() == hostname or (n.startswith('*.') and hostname.count('.') == n.count('.') and hostname.endswith(n[1:].lower())) for n in names)
        if not match:
            continue
        if not cert.not_valid_before_utc <= now < cert.not_valid_after_utc:
            raise ValueError('Matching certificate is not currently valid')
        key = next((k for k in keys if public(k.public_key()) == public(cert.public_key())), None)
        if key is None:
            continue
        chain = [cert]
        remaining = [c for c in certs if c != cert]
        while chain[-1].issuer != chain[-1].subject:
            parent = next((c for c in remaining if c.subject == chain[-1].issuer), None)
            if parent is None:
                break
            chain[-1].verify_directly_issued_by(parent)
            remaining.remove(parent)
            if parent.issuer == parent.subject:
                break
            chain.append(parent)
        return cert, key, chain, names
    raise ValueError('No valid certificate and matching private key cover the requested hostname')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', type=Path, required=True)
    parser.add_argument('--data-root', type=Path, required=True)
    parser.add_argument('--hostname', default='zsk.agicogroup.com')
    args = parser.parse_args()
    if os.name != 'nt' or not ctypes.windll.shell32.IsUserAnAdmin():
        raise ValueError('Run in administrator PowerShell on Windows')
    root = args.data_root.resolve()
    marker = json.loads((root / 'config/deployment.json').read_text(encoding='utf-8-sig'))
    if Path(marker['data_root']).resolve() != root or marker['stage'] != 'installed':
        raise ValueError('Expected an installed deployment matching the data directory')
    target = root / 'config/tls'
    if target.exists():
        raise ValueError('TLS directory already exists; preserving existing certificates')
    cert, key, chain, names = inspect_bundle(args.archive, args.hostname.lower())
    for folder in (root, root / 'config'):
        if folder.is_symlink() or folder.is_junction():
            raise ValueError('Certificate directory must not traverse a reparse point')
    target.mkdir()
    secured = subprocess.run(['icacls.exe', str(target), '/inheritance:r', '/grant:r',
        '*S-1-5-18:(OI)(CI)F', '*S-1-5-32-544:(OI)(CI)F'], capture_output=True)
    if secured.returncode:
        raise ValueError('Could not protect new TLS directory; no private key was written')
    (target / 'fullchain.pem').write_bytes(b''.join(c.public_bytes(serialization.Encoding.PEM) for c in chain))
    (target / 'private.key').write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    import ssl
    ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER).load_cert_chain(str(target / 'fullchain.pem'), str(target / 'private.key'))
    print(json.dumps({'status':'certificate_imported', 'hostname':args.hostname,
        'certificate_names':names, 'expires_utc':cert.not_valid_after_utc.isoformat(),
        'private_key_match':True, 'tls_directory':str(target),
        'https_listener_configured':False},ensure_ascii=True))

if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print('Certificate preparation failed: '+type(error).__name__+': '+str(error).encode('ascii',errors='backslashreplace').decode('ascii'))
        raise SystemExit(1)
