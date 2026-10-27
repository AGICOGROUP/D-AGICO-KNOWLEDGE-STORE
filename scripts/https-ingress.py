"""Render the owned HTTPS ingress and verify TLS and anonymous access controls."""
import argparse
import http.client
import json
from pathlib import Path
import re
import socket
import ssl


def render(data_root, hostname, port, *, mode='maintenance', backend_port=8765):
    root = Path(data_root).resolve().as_posix()
    if not re.fullmatch(r'[a-z0-9]+(?:[.-][a-z0-9]+)*', hostname) or not 1024 <= port <= 65535:
        raise ValueError('Invalid HTTPS hostname or port')
    if any(c in root for c in '\"\r\n$;{}'):
        raise ValueError('Unsupported data directory')
    if mode not in {'maintenance','proxy'} or not isinstance(backend_port,int) or not 1024 <= backend_port <= 65535 or backend_port==port:
        raise ValueError('Invalid HTTPS mode or loopback backend port')
    log = root + '/logs/https'
    tls = root + '/config/tls'
    application = """            add_header Retry-After 300 always;
            return 503 'AGICO knowledge base deployment in progress. DingTalk access will be enabled after configuration.\\n';"""
    if mode=='proxy':
        application=f'''            proxy_pass http://127.0.0.1:{backend_port};
            proxy_http_version 1.1;
            proxy_set_header Host $http_host;
            proxy_set_header X-Forwarded-Host $http_host;
            proxy_set_header X-Forwarded-Proto https;
            proxy_set_header X-Forwarded-For $remote_addr;
            proxy_set_header Connection "";
            proxy_buffering off;
            proxy_request_buffering off;
            proxy_read_timeout 3600s;
            proxy_send_timeout 3600s;
            proxy_redirect off;'''
    return f'''daemon off;
worker_processes 1;
error_log "{log}/error.log" warn;
pid "{log}/nginx.pid";
events {{ worker_connections 1024; }}
http {{
    server_tokens off;
    access_log off;
    client_max_body_size 512m;
    client_body_timeout 300s;
    client_body_temp_path "{log}/body";
    proxy_temp_path "{log}/proxy";
    fastcgi_temp_path "{log}/fastcgi";
    uwsgi_temp_path "{log}/uwsgi";
    scgi_temp_path "{log}/scgi";
    server {{
        listen 0.0.0.0:{port} ssl;
        server_name {hostname};
        ssl_certificate "{tls}/fullchain.pem";
        ssl_certificate_key "{tls}/private.key";
        ssl_protocols TLSv1.2 TLSv1.3;
        ssl_session_cache shared:AGICOTLS:10m;
        ssl_session_tickets off;
        default_type text/plain;
        add_header X-Content-Type-Options nosniff always;
        add_header Cache-Control no-store always;
        if ($host != {hostname}) {{ return 444; }}
        location = /_agico/https-ready {{
            default_type application/json;
            return 200 '{{"status":"https_ready","mode":"{mode}"}}';
        }}
        location / {{
{application}
        }}
    }}
}}
'''


def probe(hostname, port, cafile, connect='127.0.0.1', *, expected_mode='maintenance'):
    context = ssl.create_default_context(cafile=str(cafile))
    context.verify_flags |= ssl.VERIFY_X509_PARTIAL_CHAIN
    with socket.create_connection((connect, port), timeout=5) as raw:
        with context.wrap_socket(raw, server_hostname=hostname) as tls:
            tls.sendall(f'GET /_agico/https-ready HTTP/1.1\r\nHost: {hostname}:{port}\r\nConnection: close\r\n\r\n'.encode('ascii'))
            chunks = []
            while block := tls.recv(65536):
                chunks.append(block)
            response = b''.join(chunks)
            if not response.startswith(b'HTTP/1.1 200 ') or b'"status":"https_ready"' not in response:
                raise ValueError('HTTPS readiness response did not match')
            if f'"mode":"{expected_mode}"'.encode() not in response:
                raise ValueError('HTTPS readiness mode did not match')
            return {'status':'https_ready', 'mode':expected_mode, 'tls':tls.version(), 'certificate_hostname_verified':True}


def verify_application(hostname, port, cafile=None, *, backend=False):
    """Read-only checks; connect to loopback while validating the public TLS name."""
    def request(path):
        if backend:
            conn=http.client.HTTPConnection('127.0.0.1',port,timeout=8)
        else:
            context=ssl.create_default_context(cafile=str(cafile))
            context.verify_flags |= ssl.VERIFY_X509_PARTIAL_CHAIN
            conn=http.client.HTTPSConnection(hostname,port,timeout=8,context=context)
            conn._create_connection=lambda address,timeout,source_address=None: socket.create_connection(('127.0.0.1',port),timeout)
        try:
            headers={'Host':f'{hostname}:{port}'} if not backend else {}
            conn.request('GET',path,headers=headers)
            response=conn.getresponse()
            return response.status,response.read(1048576)
        finally:
            conn.close()
    status,body=request('/v1/auth/settings')
    if status!=200 or json.loads(body).get('auth_mode')!='dingtalk':
        raise ValueError('DingTalk authentication is not active')
    status,body=request('/health/ready')
    if status!=200 or json.loads(body).get('status')!='ready':
        raise ValueError('Backend database is not ready')
    status,_=request('/v1/documents')
    if status!=401:
        raise ValueError('Anonymous document access was not denied')
    status,_=request('/')
    if status!=200:
        raise ValueError('Knowledge-base home page is unavailable')
    return {'status':'application_verified','auth_mode':'dingtalk','anonymous_documents':401,'database_ready':True}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('action', choices=['render','probe','verify-backend','verify-application'])
    parser.add_argument('--data-root', type=Path, required=True)
    parser.add_argument('--hostname', required=True)
    parser.add_argument('--port', type=int, required=True)
    parser.add_argument('--mode', choices=['maintenance','proxy'],default='maintenance')
    parser.add_argument('--backend-port',type=int,default=8765)
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    if args.action=='render':
        target=args.output or args.data_root/'config/https/nginx.conf'
        if target.resolve().parent != (args.data_root/'config/https').resolve():
            raise ValueError('Output must be in the owned HTTPS config directory')
        target.write_text(render(args.data_root,args.hostname,args.port,mode=args.mode,backend_port=args.backend_port),encoding='utf-8')
    elif args.action=='probe':
        print(json.dumps(probe(args.hostname,args.port,args.data_root/'config/tls/fullchain.pem',expected_mode=args.mode)))
    elif args.action=='verify-backend':
        print(json.dumps(verify_application(args.hostname,args.backend_port,backend=True)))
    else:
        print(json.dumps(verify_application(args.hostname,args.port,args.data_root/'config/tls/fullchain.pem')))

if __name__=='__main__':
    try:
        main()
    except Exception as error:
        # Checks contain no credentials; bounded stdout avoids native stderr
        # terminating a PowerShell 5.1 readiness retry before exit-code handling.
        detail=str(error) if isinstance(error,ValueError) else type(error).__name__
        print(json.dumps({'status':'https_check_failed','detail':detail}))
        raise SystemExit(1)
