"""OCR worker admission and reference metrics. No document leaves this module.

Existing PDF/DOCX extraction and attachment owner boundaries remain authoritative.
An OCR engine is not registered until real GPU and multilingual acceptance.
"""
from decimal import Decimal
import unicodedata


def route_pages(texts, *, enabled=False, consent=False, max_pages=20,
                estimated_usd='0', budget_usd='0'):
    if not isinstance(texts,list) or not texts or any(not isinstance(t,str) for t in texts):raise ValueError('invalid_pages')
    missing=[i+1 for i,t in enumerate(texts) if not t.strip()]
    if not missing:return {'route':'existing_native_extractor','ocr_pages':[]}
    reason='ocr_not_enabled'
    if enabled:
        reason='document_consent_required' if not consent else 'page_limit_exceeded'
        if consent and len(missing)<=max_pages:
            estimate,budget_value=Decimal(estimated_usd),Decimal(budget_usd)
            if not estimate.is_finite() or not budget_value.is_finite() or estimate<0 or budget_value<0:raise ValueError('invalid_ocr_budget')
            reason='ocr_budget_exceeded' if estimate>budget_value else 'worker_acceptance_required'
    return {'route':'blocked','ocr_pages':missing,'reason':reason}


def distance(a,b):
    if len(a)*len(b)>4000000:raise ValueError('metric_input_too_large')
    row=list(range(len(b)+1))
    for i,x in enumerate(a,1):
        new=[i]
        for j,y in enumerate(b,1):new.append(min(new[-1]+1,row[j]+1,row[j-1]+(x!=y)))
        row=new
    return row[-1]


def metrics(reference, extracted):
    if not isinstance(reference,str) or not isinstance(extracted,str) or not reference:raise ValueError('reference_required')
    a=unicodedata.normalize('NFC',reference);b=unicodedata.normalize('NFC',extracted)
    words=a.split()
    return {'cer':distance(a,b)/len(a),'wer':distance(words,b.split())/len(words) if words else None,
            'language_quality_verified':False}


def table_accuracy(reference,extracted):
    if not isinstance(reference,list) or not reference or any(not isinstance(row,list) or not row for row in reference):raise ValueError('reference_table_required')
    if not isinstance(extracted,list) or len(reference)!=len(extracted) or any(not isinstance(b,list) or len(a)!=len(b) for a,b in zip(reference,extracted)):
        return {'shape_match':False,'cell_accuracy':0.0}
    total=sum(len(row) for row in reference)
    correct=sum(str(a).strip()==str(b).strip() for left,right in zip(reference,extracted) for a,b in zip(left,right))
    return {'shape_match':True,'cell_accuracy':correct/total}
