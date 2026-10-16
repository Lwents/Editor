"""Deterministic coverage checks and review warnings, not accuracy scores."""
import re
from collections import defaultdict


def audit_translation(source,target):
    errors=[];warnings=[]
    if len(source)!=len(target):
        errors.append({'code':'CUE_COUNT_MISMATCH','source':len(source),'target':len(target)})
    repeated=defaultdict(list)
    for index,(original,translated) in enumerate(zip(source,target),1):
        if (original.start,original.end)!=(translated.start,translated.end):
            errors.append({'id':index,'code':'TIMING_CHANGED'})
        if not translated.text.strip():
            errors.append({'id':index,'code':'EMPTY_TRANSLATION'})
        if re.search(r'[\u3400-\u9fff]',translated.text):
            errors.append({'id':index,'code':'UNTRANSLATED_CJK'})
        normalized=re.sub(r'\W+','',translated.text.casefold())
        if len(normalized)>=20:
            repeated[normalized].append(index)
        duration=translated.end-translated.start
        if duration>0 and len(translated.text)/duration>30:
            warnings.append({'id':index,'time':original.start,'code':'READING_SPEED','reason':'Câu dài so với thời gian hiển thị; cần xem lại cách chia câu.'})
    for ids in repeated.values():
        if len(ids)>=3 and len({source[i-1].text for i in ids})>=3:
            for item_id in ids:
                warnings.append({'id':item_id,'time':source[item_id-1].start,'code':'REPEATED_TRANSLATION','reason':'Bản dịch dài bị lặp cho các câu nguồn khác nhau; cần kiểm tra.'})
    return {'version':1,'source_cues':len(source),'translated_cues':len(target),
            'errors':errors,'warnings':warnings,'accuracy_percent':None,
            'note':'Kiểm tra cấu trúc/độ bao phủ nguồn đã nhận diện, không phải phần trăm chính xác toàn video.'}
