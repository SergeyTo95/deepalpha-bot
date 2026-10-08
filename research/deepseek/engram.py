"""Bounded CPU addressing reference and experiment budget, not trained memory.

Quantum already has Qwen PLE. This prototype is for an ablation against PLE,
not an additive production table. No tokenizer or model is downloaded here.
"""
import hashlib


def addresses(tokens, buckets=4096, heads=2, orders=(2,3), max_tokens=8192):
    if not 16<=buckets<=1048576 or not 1<=heads<=8 or not orders or any(n not in {2,3,4} for n in orders):
        raise ValueError('invalid_lookup_budget')
    if len(tokens)>max_tokens or any(type(t) is not int or not 0<=t<2**31 for t in tokens):
        raise ValueError('invalid_token_sequence')
    result=[]
    for position in range(len(tokens)):
        row=[]
        for order in orders:
            prefix=[0]*max(0,order-position-1)+tokens[max(0,position-order+1):position+1]
            data=b''.join(t.to_bytes(4,'little') for t in prefix)
            for head in range(heads):
                salt=bytes([order,head]);row.append(int.from_bytes(hashlib.blake2b(salt+data,digest_size=8).digest(),'little')%buckets)
        result.append(row)
    return result


def budget(buckets=4096, heads=2, orders=2, width=32, hidden=2560, layers=2, bytes_per_weight=2):
    values=[buckets,heads,orders,width,hidden,layers,bytes_per_weight]
    if any(type(v) is not int or v<=0 for v in values):raise ValueError('invalid_budget')
    if buckets>1048576 or heads>8 or orders>3 or width>256 or layers>4 or hidden>8192 or bytes_per_weight>4:
        raise ValueError('experiment_budget_exceeded')
    table=buckets*heads*orders*width*layers*bytes_per_weight
    projections=2*heads*orders*width*hidden*layers*bytes_per_weight
    return {'table_bytes':table,'projection_bytes':projections,'weight_bytes':table+projections,
            'training_optimizer_activation_bytes':None,'quality_gain':None,'checkpoint_created':False,
            'quantum_existing_ple_must_be_baseline':True}
