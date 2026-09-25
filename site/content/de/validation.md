---
title: Validierung
description: Die Gate-Kette — was „validiert" bedeutet und warum Ablehnungen veröffentlicht werden.
lang: de
translation-status: complete
source-revision: en:validation@2026-09-02
---

Validierung ist der Grund, warum die Plattform existiert. Ein Fehlschlag an irgendeinem Gate ist für diesen Kandidaten terminal — keine Mittelwerte, keine Ausnahmen.

## Die Gate-Kette

| Ebene | Gates |
| :--- | :--- |
| Datensatz | Qualitäts-Gates · Fingerprint · Provenienz |
| Features | Schema-Hash · Dimension · Grenzen · Ordnung |
| Modell | 10-Gate-Load-Gate · Kalibrierung · Mindest-Evidenz |
| Forschung | gepurgtes/embargo Walk-Forward · OOS-Untergrenzen · Robustheit |
| Governance | 14-Gate-Verifikation · Promotion-Transaktion |
| Runtime | forensisches Deploy-Gate vor jedem Release |
| Release | SHA-256 · Manifeste · SBOM · Verifikation nach Veröffentlichung |

## OOS-Untergrenzen (Modell-Lebenszyklus)

macro-F1 ≥ 0.34 · balancierte Genauigkeit ≥ 0.34 · ECE ≤ 0.15 · Evidenz ≥ 100 Zeilen.

## Das Gate beißt — öffentlicher Beweis

Der 70D-Liquiditätskandidat — die am stärksten gearbeitete Forschungsserie des Repositories — wurde genau durch diese Kette abgelehnt (OOS NOT_ELIGIBLE), nachdem Walk-Forward- und Shadow-Benchmarks mit echten Daten negativ ausfielen. Der Live-Vertrag blieb bei 50D. Ein Gate, das nichts ablehnt, ist Dekoration; dieses hat eine öffentliche Ablehnung in seinem Buch.

Vertiefung: [out-of-sample.md](https://github.com/Opselon/NexusTradingForexBot/blob/main/docs/research/out-of-sample.md)
· [validation.md](https://github.com/Opselon/NexusTradingForexBot/blob/main/docs/research/validation.md)
· [reproducibility.md](https://github.com/Opselon/NexusTradingForexBot/blob/main/docs/research/reproducibility.md).
