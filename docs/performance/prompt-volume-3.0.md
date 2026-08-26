# 3.0 Prompt Volume Baseline

The deterministic prompt gate compares the fixed text of four current AI flows
with the tagged `v2.9.0` implementation: the quality workspace prompt, quality
chapter prompt, cataloging-candidate prompt, and structured creation-stage prompt.

Run `backend/.venv/Scripts/python.exe backend/scripts/check_prompt_budget.py`
from the repository root. CI fails if the combined reduction falls below 20%.
The current total is 8,057 characters versus 30,529 in 2.9, a 73.61%
reduction, and every individual flow clears the 20% threshold. Runtime context and author-authored prompt overrides are excluded
because they are task data rather than fixed controller instructions.
