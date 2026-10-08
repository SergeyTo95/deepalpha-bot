"""CPU experiment configuration and evidence gate; does not modify a worker."""
from .evaluation import comparison

FLASH_RUNTIME='01ae597e3f7d4742909e1e831abb12fe3d24b2cf'


def ngram_trial_args(runtime_revision, help_text):
    # Exact pinned runtime source was audited for these flags. Actual binary must
    # advertise them too; unsupported binaries never receive invented arguments.
    flags=['--spec-type','--spec-ngram-simple-size-n','--spec-ngram-simple-size-m','--spec-ngram-simple-min-hits']
    if runtime_revision!=FLASH_RUNTIME or any(f not in help_text for f in flags):
        raise ValueError('unqualified_speculative_runtime')
    return ['--spec-type','ngram-simple','--spec-ngram-simple-size-n','3',
            '--spec-ngram-simple-size-m','4','--spec-ngram-simple-min-hits','2']


def admission(baseline, candidate, max_extra_rss_mib=256):
    report=comparison(baseline,candidate);reasons=[]
    for field in ['model','checkpoint_revision','runtime_revision']:
        if not baseline.get(field) or baseline[field]!=candidate.get(field):reasons.append('changed_'+field)
    if report['regressions']:reasons.append('reference_regressions')
    # This is an initial diagnostic gate, never an automatic rollout gate.
    a=report['baseline']['metrics'];b=report['candidate']['metrics']
    for field in ['ttft_seconds','reported_decode_tps','server_rss_mib','server_cpu_seconds']:
        if min(a[field]['samples'],b[field]['samples'])<3:reasons.append('missing_'+field)
    if not reasons:
        if b['reported_decode_tps']['median']<a['reported_decode_tps']['median']*1.10:reasons.append('decode_gain_below_10_percent')
        if b['ttft_seconds']['median']>a['ttft_seconds']['median']*1.05:reasons.append('ttft_regression')
        if b['server_rss_mib']['median']>a['server_rss_mib']['median']+max_extra_rss_mib:reasons.append('rss_budget_exceeded')
        if b['server_cpu_seconds']['median']>a['server_cpu_seconds']['median']*1.05:reasons.append('cpu_cost_regression')
    return {'eligible_for_further_trials':not reasons,'reasons':reasons,'automatic_release_allowed':False,
            'requires':'Repeated warm/cold interleaved runs, 2K tokenized prompts, tokenizer identity, greedy token equality and sampled-distribution checks, independent quality holdout and operator review.'}
