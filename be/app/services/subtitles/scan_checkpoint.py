"""Persist scan observations, never pixels, with an overlap on resume."""
from pathlib import Path
from app.services.persistence import atomic_json, read_json


class ScanCheckpoint:
    def __init__(self, path: Path, key: str, step: int, fps: float):
        self.path,self.key,self.step,self.fps=path,key,step,fps

    def load(self):
        saved=read_json(self.path,lambda value:isinstance(value,dict))
        if not saved or saved.get('key') != self.key:
            return None
        last=saved.get('next_frame',0)
        timestamps=saved.get('timestamps',[])
        samples=saved.get('samples',[])
        if type(last) is not int or last<0 or len(timestamps)!=last or not isinstance(samples,list):
            raise RuntimeError('OCR_CHECKPOINT_INVALID: Giữ nguyên checkpoint OCR để khôi phục.')
        start=max(0,last-round(self.fps*2))
        start-=start%self.step
        return {'start':start,'regions':saved['regions'], 'timestamps':timestamps[:start],
                'samples':[row for row in samples if row[0]<start],
                'results':{int(key):tuple(value) for key,value in saved['results'].items() if int(key)<start},
                'fresh':{value for value in saved['fresh'] if value<start},
                'fallback':{value for value in saved['fallback'] if value<start}}

    def save(self,next_frame,regions,timestamps,samples,results,fresh,fallback):
        atomic_json(self.path,{'key':self.key,'next_frame':next_frame,'regions':regions,
            'timestamps':timestamps[:next_frame], 'samples':samples,'results':results,
            'fresh':sorted(fresh),'fallback':sorted(fallback)})
