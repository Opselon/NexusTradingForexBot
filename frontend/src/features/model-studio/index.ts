/** Neural Model Studio feature — deep learning inspection, prediction, 70D assembly & training. */
import { defineFeature, featurePage } from "@/app/featureModule";

export default defineFeature({
  meta: {
    route: "/model-studio",
    label: "Neural Studio",
    icon: "🧠",
    section: "MARKET & RESEARCH",
    legacyTab: "tab-model-studio",
    keywords: "deep learning neural network pytorch scalpnet 50d 70d train predict saliency entropy",
  },
  lazy: featurePage(() => import("./ui/ModelStudioPage")),
});
