"""Install the current checkout as a per-user macOS login service."""
import os
import plistlib
import subprocess
from pathlib import Path
root=Path(__file__).resolve().parent
folder=Path.home()/'Library/LaunchAgents'
folder.mkdir(parents=True,exist_ok=True)
path=folder/'local.rainkeeper.plist'
config={'Label':'local.rainkeeper','ProgramArguments':[str(root/'.venv/bin/python'),'-m','uvicorn','app:app','--host','127.0.0.1','--port','8080'],'WorkingDirectory':str(root),'RunAtLoad':True,'KeepAlive':True,'ThrottleInterval':30,'StandardOutPath':str(root/'data/service.log'),'StandardErrorPath':str(root/'data/service-error.log')}
path.write_bytes(plistlib.dumps(config))
subprocess.run(['launchctl','bootstrap',f'gui/{os.getuid()}',str(path)],check=True)
print('Installed local.rainkeeper login service.')
