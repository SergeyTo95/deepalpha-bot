"""Review gate into the existing Model Lab dataset store, not another exporter."""
from .evaluation import cases, digest


def add_reviewed_candidate(lab, owner_id, candidate, *, human_approved=False,
                           rights_confirmed=False, no_personal_data=False):
    if not human_approved or not rights_confirmed or not no_personal_data:
        raise ValueError('human_rights_privacy_review_required')
    if candidate.get('split')!='train':raise ValueError('holdout_not_training')
    prompt=candidate.get('prompt');target=candidate.get('target')
    if not isinstance(prompt,str) or not isinstance(target,str) or not prompt.strip() or not target.strip():
        raise ValueError('invalid_training_candidate')
    if len(prompt)>6000 or len(target)>16000:raise ValueError('training_candidate_too_large')
    exams=cases()+lab.builtin_cases()
    normalize=lambda text:' '.join(text.split()).casefold()
    forbidden={normalize(c['messages'][-1]['content']) for c in exams}
    if normalize(prompt) in forbidden:raise ValueError('builtin_exam_leakage')
    if candidate.get('reference_verified') is not True or not candidate.get('reference_source'):
        raise ValueError('independent_reference_required')
    # Existing owner authority, PostgreSQL, audit and train/holdout exporter apply.
    identifier=lab.add_example(owner_id,prompt,target,'train',True)
    return {'id':identifier,'source_digest':digest(candidate['reference_source']),
            'semantic_leakage_review_required':True,'trained_checkpoint_created':False}
