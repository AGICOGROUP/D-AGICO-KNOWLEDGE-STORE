"""Read-only OpenCV loader probe. No package installation or system changes."""
import ctypes,json,os,struct,subprocess,sys
from pathlib import Path

def imports(path):
    b=path.read_bytes()
    pe=struct.unpack_from('<I',b,0x3c)[0]
    if b[pe:pe+4]!=b'PE\0\0':raise ValueError('Invalid PE')
    n=struct.unpack_from('<H',b,pe+6)[0];size=struct.unpack_from('<H',b,pe+20)[0]
    opt=pe+24;magic=struct.unpack_from('<H',b,opt)[0]
    directory=opt+(112 if magic==0x20b else 96)
    rva=struct.unpack_from('<I',b,directory+8)[0]
    sections=opt+size
    def offset(r):
        for i in range(n):
            vs,va,rawsize,raw=struct.unpack_from('<IIII',b,sections+i*40+8)
            if va<=r<va+max(vs,rawsize):return raw+r-va
        raise ValueError('RVA outside sections')
    pos=offset(rva);found=[]
    while any(b[pos:pos+20]):
        name=struct.unpack_from('<I',b,pos+12)[0];start=offset(name)
        found.append(b[start:b.index(b'\0',start)].decode('ascii'));pos+=20
    return found

if len(sys.argv)>1 and sys.argv[1]=='--dll':
    ctypes.windll.kernel32.SetErrorMode(3)
    dll_dirs=[os.add_dll_directory(folder) for folder in sys.argv[3:]]
    try:
        ctypes.WinDLL(sys.argv[2]);print(json.dumps({'dll':Path(sys.argv[2]).name,'status':'loaded'}))
    except OSError as e:
        print(json.dumps({'dll':Path(sys.argv[2]).name,'winerror':e.winerror,'message':str(e)},ensure_ascii=True))
    sys.exit(0)

if __name__=='__main__':
    ctypes.windll.kernel32.SetErrorMode(3)
    import importlib.util
    spec=importlib.util.find_spec('cv2')
    if not spec:raise SystemExit('cv2 package missing')
    folder=Path(spec.origin).parent
    candidates=list(folder.glob('*.pyd'))
    if len(candidates)!=1:raise SystemExit('Expected one cv2.pyd')
    pyd=candidates[0]
    base=str(Path(sys._base_executable).parent)
    print('OpenCV read-only loader diagnosis',flush=True)
    for dll in imports(pyd):
        try:
            r=subprocess.run([sys.executable,'-X','utf8',__file__,'--dll',dll,str(folder),base],capture_output=True,timeout=20)
            print(r.stdout.decode('utf-8',errors='backslashreplace').strip() or 'Loader process exit '+str(r.returncode),flush=True)
        except subprocess.TimeoutExpired:print('DLL probe timeout: '+dll,flush=True)
    result=subprocess.run([sys.executable,'-X','utf8','-c','import cv2; print(cv2.__version__)'],capture_output=True,timeout=40)
    print('Actual cv2 import exit: '+str(result.returncode),flush=True)
    for data in (result.stdout,result.stderr):
        print(data.decode('utf-8',errors='backslashreplace').encode('ascii',errors='backslashreplace').decode('ascii'),flush=True)

