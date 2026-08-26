# Recover.ai

RecoverAI detects failed Razorpay subscription payments, applies bounded recovery
actions, and records the full decision trail.

## Phase 5: Synthetic Measurement

Run the deterministic demo batch from `backend/`:

```powershell
.\.venv\Scripts\python.exe -m scripts.run_synthetic_batch --count 60 --seed 20260826
```

Baseline result from the 60-case batch:

- Cases processed: 60
- Recovered: 14 (23.3%)
- Amount recovered: Rs 10,886.00
- Still at risk: Rs 37,354.00
- Exceptions retained: 46

The batch uses a simulated Razorpay client. It exercises Diagnose -> Decide ->
Guardrail -> Act and the due-retry worker without making live payment calls.