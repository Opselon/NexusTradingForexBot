"""MODEL PROVISIONING — first-run model acquisition architecture (BUG-293 redesign).

Honest product contract (operator directive 2026-09-16): a fresh install
reaches a usable serving model through TWO explicit, user-chosen paths —

  PATH A  ``OfficialRemoteSource``  — download the official Nexus model
          bundle (dataset + weights), verify signature/SHA256/schema/
          geometry/integrity BEFORE it may occupy the serving slot.
          No PyTorch needed to install; verification uses pure-python +
          torch.load(weights_only) for the geometry probe on any host that
          has torch; NO torch is required for download + hash + signature
          + manifest validation (the torch geometry probe is the final
          gate, run at load time by the engine either way).

  PATH B  ``LocalTrainingSource``   — train locally on the user's OWN
          broker export (CSV / Parquet / MT5 columns). User data never
          leaves the machine: import -> validation/diagnostics ->
          canonical 70D feature pipeline -> purged walk-forward (time
          splits only, never random) -> integrity verification -> local
          install as CANDIDATE. Governance unchanged: completion is not
          promotion; the registry CHAMPION row is only ever written by
          the governed promotion lifecycle.

  FALLBACK ``mint_starter_bundle`` (release.bootstrap) — a deterministic
  DEV STARTER only: explicitly labeled, loudly surfaced, replaces
  nothing. It exists for offline boots, CI and emergency recovery, and
  is classified apart from official/user/governed models by origin.

``ExistingLocalSource`` classifies whatever is already in the serving slot
(governed champion / official / user-trained / dev-starter / unservable)
so the UI and CLI always tell the truth about what is serving.
"""

from nexus_scalp.model_provisioning.official import (
    OfficialBundleError,
    OfficialBundleSource,
    VerifiedBundle,
)
from nexus_scalp.model_provisioning.pipeline import (
    ProgressEvent,
    TrainingCancelled,
    TrainingRequest,
    UserBarsImport,
    import_user_bars,
    train_local_model,
)
from nexus_scalp.model_provisioning.service import (
    ORIGIN_DEV_STARTER,
    ORIGIN_GOVERNED,
    ORIGIN_OFFICIAL,
    ORIGIN_USER_TRAINED,
    FirstRunCoordinator,
    SlotClassification,
    classify_serving_slot,
    provisioner_state_path,
    read_provisioner_state,
    write_provisioner_state,
)
from nexus_scalp.model_provisioning.states import LifecycleState

__all__ = [
    "ORIGIN_DEV_STARTER",
    "ORIGIN_GOVERNED",
    "ORIGIN_OFFICIAL",
    "ORIGIN_USER_TRAINED",
    "FirstRunCoordinator",
    "LifecycleState",
    "OfficialBundleError",
    "OfficialBundleSource",
    "ProgressEvent",
    "SlotClassification",
    "TrainingCancelled",
    "TrainingRequest",
    "UserBarsImport",
    "VerifiedBundle",
    "classify_serving_slot",
    "import_user_bars",
    "provisioner_state_path",
    "read_provisioner_state",
    "train_local_model",
    "write_provisioner_state",
]
