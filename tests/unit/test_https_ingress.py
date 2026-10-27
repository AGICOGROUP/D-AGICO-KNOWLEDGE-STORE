import importlib.util
from pathlib import Path
import socket
import subprocess
import time

import pytest

spec=importlib.util.spec_from_file_location('https_ingress',Path(__file__).resolve().parents[2]/'scripts/https-ingress.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)


def test_rejects_hostname_injection(tmp_path):
    with pytest.raises(ValueError):module.render(tmp_path,'example.com; return 200;',8888)


def test_proxy_requires_explicit_mode_and_loopback_port(tmp_path):
    assert 'proxy_pass' not in module.render(tmp_path,'zsk.agicogroup.com',8888)
    with pytest.raises(ValueError):module.render(tmp_path,'zsk.agicogroup.com',8888,mode='unknown')
    with pytest.raises(ValueError):module.render(tmp_path,'zsk.agicogroup.com',8888,mode='proxy',backend_port=8888)


def test_native_proxy_preserves_origin_and_anonymous_access_control(tmp_path):
    import http.server
    import json
    import threading
    from test_https_certificate_bundle import test_fresh_certificate_bundle, module as certificates
    from cryptography.hazmat.primitives import serialization

    observed=[]
    class Backend(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            observed.append(dict(self.headers))
            if self.path=='/v1/auth/settings':status,body=200,{'auth_mode':'dingtalk'}
            elif self.path=='/v1/documents':status,body=401,{'detail':'Unauthorized'}
            elif self.path=='/health/ready':status,body=200,{'status':'ready'}
            else:status,body=200,{'page':'synthetic-test-only'}
            payload=json.dumps(body).encode()
            self.send_response(status);self.send_header('Content-Length',str(len(payload)))
            self.end_headers();self.wfile.write(payload)
        def log_message(self,*args):pass

    backend=http.server.ThreadingHTTPServer(('127.0.0.1',0),Backend)
    worker=threading.Thread(target=backend.serve_forever,daemon=True);worker.start()
    test_fresh_certificate_bundle(tmp_path,'valid')
    cert,key,*_=certificates.inspect_bundle(tmp_path/'new-test-only.zip','zsk.agicogroup.com')
    tls=tmp_path/'config/tls';tls.mkdir(parents=True)
    (tls/'fullchain.pem').write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    (tls/'private.key').write_bytes(key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
    logs=tmp_path/'logs/https';logs.mkdir(parents=True)
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    nginx=Path(__file__).resolve().parents[2]/'.local/https-proxy-20261026/nginx.exe'
    process=None
    try:
        if not nginx.is_file():pytest.skip('Official native NGINX cache required')
        conf=tmp_path/'nginx.conf'
        conf.write_text(module.render(tmp_path,'zsk.agicogroup.com',port,mode='proxy',backend_port=backend.server_port))
        args=[str(nginx),'-p',logs.as_posix()+'/', '-c',str(conf),'-e',str(logs/'error.log')]
        check=subprocess.run(args+['-t'],capture_output=True)
        assert check.returncode==0,check.stderr.decode(errors='replace')
        process=subprocess.Popen(args,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        for _ in range(30):
            try:
                result=module.probe('zsk.agicogroup.com',port,tls/'fullchain.pem',expected_mode='proxy');break
            except (OSError,ValueError):time.sleep(.1)
        else:pytest.fail('Proxy TLS ingress did not become ready')
        assert result['mode']=='proxy'
        assert module.verify_application('zsk.agicogroup.com',port,tls/'fullchain.pem')['anonymous_documents']==401
        assert any(h.get('Host')==f'zsk.agicogroup.com:{port}' and h.get('X-Forwarded-Proto')=='https' for h in observed)
        assert all(h.get('X-Forwarded-For')=='127.0.0.1' for h in observed)
        # A healthy HTTP service without DingTalk authentication cannot pass publication checks.
        original=Backend.do_GET
        def unsafe(self):
            if self.path=='/v1/documents':self.path='/'
            return original(self)
        Backend.do_GET=unsafe
        with pytest.raises(ValueError,match='Anonymous'):
            module.verify_application('zsk.agicogroup.com',port,tls/'fullchain.pem')
    finally:
        if process is not None:
            subprocess.run(args+['-s','quit'],capture_output=True,timeout=10)
            try:process.wait(timeout=10)
            except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)
        backend.shutdown();backend.server_close();worker.join(timeout=5)


def test_native_tls_checks_hostname_and_keeps_application_private(tmp_path):
    import ssl
    from test_https_certificate_bundle import test_fresh_certificate_bundle, module as certificates
    test_fresh_certificate_bundle(tmp_path,'valid')
    cert,key,chain,names=certificates.inspect_bundle(tmp_path/'new-test-only.zip','zsk.agicogroup.com')
    from cryptography.hazmat.primitives import serialization
    tls=tmp_path/'config/tls';tls.mkdir(parents=True)
    (tls/'fullchain.pem').write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    (tls/'private.key').write_bytes(key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
    logs=tmp_path/'logs/https';logs.mkdir(parents=True)
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    conf=tmp_path/'nginx.conf';conf.write_text(module.render(tmp_path,'zsk.agicogroup.com',port))
    nginx=Path(__file__).resolve().parents[2]/'.local/https-proxy-20261026/nginx.exe'
    if not nginx.is_file():pytest.skip('Official native NGINX cache required')
    args=[str(nginx),'-p',logs.as_posix()+'/', '-c',str(conf)]
    check=subprocess.run(args+['-t'],capture_output=True)
    assert check.returncode==0,check.stderr.decode(errors='replace')
    process=subprocess.Popen(args,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    try:
        for _ in range(30):
            try:
                result=module.probe('zsk.agicogroup.com',port,tls/'fullchain.pem');break
            except (OSError,ValueError):time.sleep(.1)
        else:pytest.fail('TLS ingress did not become ready')
        assert result['certificate_hostname_verified'] is True
        context=ssl.create_default_context(cafile=str(tls/'fullchain.pem'))
        with socket.create_connection(('127.0.0.1',port)) as raw:
            with context.wrap_socket(raw,server_hostname='zsk.agicogroup.com') as conn:
                conn.sendall(f'GET /api/auth/session HTTP/1.1\r\nHost: zsk.agicogroup.com:{port}\r\nConnection: close\r\n\r\n'.encode())
                assert conn.recv(65536).startswith(b'HTTP/1.1 503 ')
        with socket.create_connection(('127.0.0.1',port)) as raw:
            with pytest.raises(ssl.SSLCertVerificationError):context.wrap_socket(raw,server_hostname='wrong.agicogroup.com')
    finally:
        subprocess.run(args+['-s','quit'],capture_output=True,timeout=10)
        try:process.wait(timeout=10)
        except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)
