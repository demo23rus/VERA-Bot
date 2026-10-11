import importlib.util,sys,subprocess
from pathlib import Path
import yaml
s=importlib.util.spec_from_file_location('gpu_route_test',Path(__file__).with_name('ngi_worker_router.py'))
r=importlib.util.module_from_spec(s);sys.modules[s.name]=r;s.loader.exec_module(r)
def test_gpu_dispatch(monkeypatch,tmp_path):
 c=yaml.safe_load((Path(__file__).resolve().parents[1]/'.ngi/workers.yaml').read_text())
 assert c['policy']['primary_worker']=='gpu_local' and c['policy']['fallback_order']==[]
 assert all(not v.get('automatic') for k,v in c['workers'].items() if k!='gpu_local')
 monkeypatch.setattr(r,'load_config',lambda:c)
 monkeypatch.setattr(r,'worker_eligibility',lambda *a:(True,'ok'))
 monkeypatch.setattr(r,'worker_prompt',lambda *a:'test')
 monkeypatch.setattr(r,'write_job',lambda *a:None)
 calls=[]
 def fake(cmd,*a,**kw):
  calls.append(cmd);return subprocess.CompletedProcess(cmd,0,'WORKER_PASS','')
 monkeypatch.setattr(r,'run',fake)
 status,_=r.run_worker(tmp_path,{'task':'test','worktree':str(tmp_path),'backend_worktree':str(tmp_path),'frontend_worktree':str(tmp_path/'frontend')},'gpu_local')
 assert status=='WORKER_PASS'
 assert len(calls)==1 and calls[0][1].endswith('/gpu_worker.py')
