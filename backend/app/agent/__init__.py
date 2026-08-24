"""The Diagnose -> Decide -> Act agent core.

Four deterministic modules, kept free of I/O where possible so they unit-test
in isolation:

* :mod:`app.agent.diagnosis` — classify a failed payment into a root cause.
* :mod:`app.agent.policy` — map a diagnosis to a *proposed* recovery action.
* :mod:`app.agent.guardrail` — the safety gate that may veto/allow an action.
* :mod:`app.agent.executor` — the Act step: runs the loop, writes the audit
  trail *before* execution, and is the only origin of a Razorpay call.

The LLM (Gemini) is consulted only for cases the rules table cannot resolve;
the guardrail — never the LLM — has the final say on whether an action runs.
"""
