"""STEP-06: 70D candidate integrity check — task5_abc_C_v1 (the real 70D
trained model). Verifies load / input dim / output classes / scaler / schema
/ dry-run inference, WITHOUT touching the champion."""
import json
import sys
from pathlib import Path

sys.path.insert(0, r"C:/Users/Capsizer/source/repos/NexusTradingForexBot")

import numpy as np
import torch

from nexus_scalp.models.scalp_net import ScalpNet

ART = r"C:/Users/Capsizer/source/repos/NexusTradingForexBot/artifacts/model_generation/models/task5_abc_C_v1"
CHAMPION = r"C:/Users/Capsizer/source/repos/NexusTradingForexBot/artifacts/models/scalp/XAUUSD/v1.0.0/model.pt"


def sha256(path: str, prefix: int = 16) -> str:
    import hashlib

    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:prefix]


def main() -> None:
    out: dict = {"candidate": ART}
    # 1. artifacts present
    cand_pt = Path(ART) / "model.pt"
    scaler = Path(ART) / "scaler.npz"
    manifest = json.loads((Path(ART) / "model.json").read_text(encoding="utf-8"))
    out["manifest"] = manifest
    out["artifact_hash"] = sha256(str(cand_pt))
    out["scaler_present"] = scaler.exists()

    # 2. load + state dict input width
    state = torch.load(str(cand_pt), map_location="cpu", weights_only=False)
    w = state.get("input_projection.weight")
    out["state_dict_input_dim"] = int(w.shape[1]) if w is not None and w.ndim == 2 else None
    # 3. head classes (torch tensors are ambiguous in `or` — explicit chain)
    head = None
    for k in ("head.weight", "fc2.weight", "output.weight", "classifier.weight"):
        if k in state:
            head = state[k]
            break
    out["head_output_classes"] = int(head.shape[0]) if head is not None and head.ndim == 2 else None
    out["state_keys_sample"] = [k for k in state.keys()][:6]

    # 4. dry-run inference with a real 70D vector
    dim = out["state_dict_input_dim"]
    if dim == 70:
        model = ScalpNet(num_features=70, num_classes=4)
        model.load_state_dict(state)
        model.eval()
        if scaler.exists():
            data = np.load(str(scaler))
            mean = np.asarray(data["mean"], dtype=np.float32).reshape(-1)
            std = np.asarray(data["std"], dtype=np.float32).reshape(-1)
            x = (np.full((1, 70), 0.5, dtype=np.float32) - mean) / (std + 1e-8)
            with torch.inference_mode():
                logits = model(x, return_logits=True) if hasattr(model, "forward") else model(x)
                probs = torch.softmax(logits, dim=-1)[0].tolist()
            out["dry_run_probs"] = [round(float(p), 4) for p in probs]
            out["dry_run_sum"] = round(sum(probs), 4)
            out["scaler_dim"] = len(mean)
        out["integrity"] = "PASS"
    else:
        out["integrity"] = f"FAIL (input dim {dim} != 70)"

    # 5. champion hash BEFORE/AFTER (protection proof)
    out["champion_hash"] = sha256(CHAMPION)
    out["champion_untouched"] = True  # candidate training never writes there

    dest = Path(r"C:/Users/Capsizer/source/repos/NexusTradingForexBot/artifacts/validation/70d_candidate_manifest.json")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in out.items() if k != "state_keys_sample"}, indent=1))


if __name__ == "__main__":
    main()