"""Compatibility helpers between pinned RCO and Qwen4Exp routing."""
from __future__ import annotations


def router_full_probabilities(router_output):
    """Return a full per-expert probability tensor from a router output.

    Qwen4Exp returns (raw_logits, topk_weights, topk_indices), while some MoE
    implementations used by RCO return (full_softmax, topk_weights, topk_indices).
    This helper distinguishes the two without relying on a model-name string.
    """
    import torch

    if not isinstance(router_output, (tuple, list)) or len(router_output) < 1:
        raise TypeError("router output must be a tuple/list with a full expert tensor")
    first = router_output[0]
    if not torch.is_tensor(first) or first.ndim < 2:
        raise TypeError("router output[0] must be a tensor with expert dimension")

    row_sum = first.detach().float().sum(dim=-1)
    nonnegative = bool((first.detach().float().amin() >= -1e-7).item())
    normalized = bool(
        (row_sum.amin() > 0.999).item() and (row_sum.amax() < 1.001).item()
    )
    if nonnegative and normalized:
        return first.float()
    return torch.softmax(first.float(), dim=-1)


def install_qwen4exp_router_score_adapter(rco_module):
    """Patch only RCO's router-score prior for Qwen4Exp-compatible outputs.

    The Gumbel-STE optimizer, KL objective, hard-mask evaluator, and budget
    projection remain the pinned upstream implementation.
    """
    import torch
    from tqdm import tqdm

    def compute_router_scores(model, data, n_layers, n_experts, batch_size=4):
        get_input_device = rco_module.compute_router_scores.__globals__.get(
            "get_input_device"
        )
        if get_input_device is None:
            from common import get_input_device

        device = get_input_device(model)
        scores = torch.zeros(n_layers, n_experts)
        hooks = []

        for layer_index in range(n_layers):
            def make_hook(li):
                def hook_fn(module, inputs, output):
                    probs = router_full_probabilities(output)
                    scores[li] += probs.sum(dim=0).cpu()
                    return output
                return hook_fn

            gate = model.model.layers[layer_index].mlp.gate
            hooks.append(gate.register_forward_hook(make_hook(layer_index)))

        try:
            for start in tqdm(
                range(0, data.size(0), batch_size),
                desc="Quantum router scores",
            ):
                batch = data[start:start + batch_size].to(device)
                model(batch)
        finally:
            for hook in hooks:
                hook.remove()

        return scores

    rco_module.compute_router_scores = compute_router_scores
    return rco_module
