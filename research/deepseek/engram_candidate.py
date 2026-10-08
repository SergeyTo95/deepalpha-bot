"""Optional trainable ablation module. Requires separate PyTorch research env.

An original minimal design inspired by conditional n-gram lookup; it is not a
copy of the official demo and not a drop-in Quantum/Qwen4Exp implementation.
Hash addressing stays on CPU. Zero output initialization preserves the backbone
at insertion time. Training, Qwen four-stream integration and GGUF export are
separate unqualified stages.
"""
import torch
from torch import nn
from .engram import addresses, budget


class ConditionalLookup(nn.Module):
    def __init__(self, hidden=256, buckets=4096, width=32, heads=2):
        super().__init__();budget(buckets=buckets,heads=heads,width=width,hidden=hidden,layers=1)
        self.buckets,self.heads=buckets,heads
        self.tables=nn.ModuleList([nn.Embedding(buckets,width) for _ in range(heads*2)])
        self.key=nn.Linear(heads*2*width,hidden,bias=False)
        self.value=nn.Linear(heads*2*width,hidden,bias=False)
        self.norm=nn.LayerNorm(hidden)
        nn.init.zeros_(self.value.weight)
    def forward(self, hidden_states, token_ids):
        if hidden_states.device.type!='cpu' or token_ids.device.type!='cpu':raise ValueError('cpu_ablation_only')
        if hidden_states.ndim!=3 or token_ids.shape!=hidden_states.shape[:2]:raise ValueError('invalid_shapes')
        ids=torch.tensor([addresses(row.tolist(),self.buckets,self.heads) for row in token_ids],dtype=torch.long)
        memory=torch.cat([table(ids[:,:,i]) for i,table in enumerate(self.tables)],dim=-1)
        key=self.norm(self.key(memory));query=self.norm(hidden_states)
        gate=torch.sigmoid((key*query).mean(-1,keepdim=True))
        return hidden_states+gate*self.value(memory)
