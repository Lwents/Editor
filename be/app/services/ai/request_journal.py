"""Track a Gemini request before submission so reconnects can read its reply."""
import hashlib
import json
from pathlib import Path
from app.services.persistence import atomic_json, read_json


class RequestJournal:
    def __init__(self, directory: Path, scope: str, messages, policy=None, attachments=None):
        files=[]
        for file in attachments or []:
            path=Path(file)
            with path.open('rb') as stream:
                files.append(hashlib.file_digest(stream,'sha256').hexdigest())
        self.key=hashlib.sha256(json.dumps({'messages':messages,'policy':policy.cache_key if policy else None,
            'files':files},ensure_ascii=False,sort_keys=True).encode()).hexdigest()
        self.path=directory/'request-journal'/(scope+'.json')

    def load(self):
        value=read_json(self.path,lambda value:isinstance(value,dict))
        return value if value and value.get('input')==self.key else None

    def save(self,status,request_id,url,response=None):
        atomic_json(self.path,{'input':self.key,'status':status,'request_id':request_id,'url':url,'response':response})
