"""Realistic LLM-generated code carrying version-drift API mistakes.

This is apidrift's anchor fixture and future README demo. Every line apidrift will
flag is a real mistake an LLM trained on an older library version would confidently
emit. The clean lines below the divider exist to prove apidrift stays SILENT when it
should — soundness is the feature, so the fixture tests it like one.

NOTE: this file is intentionally not runnable; it is parsed, not executed.
"""

import openai
import pandas as pd
import requests
from pandas import read_csv

# (1) Removed in openai>=1.0 — the classic. Models trained on 0.x still emit this.
response = openai.ChatCompletion.create(model="gpt-4", messages=[])

# (2) `mangle_dupe_cols` was removed in pandas 2.0.
df = pd.read_csv("data.csv", mangle_dupe_cols=True)

# (3) Typo'd hallucination — there is no `pandas.read_exel`.
frame = pd.read_exel("sheet.xlsx")

# ----------------------------------------------------------------------------- #
# Everything below must stay SILENT (soundness anchors).
# ----------------------------------------------------------------------------- #

# Valid calls that genuinely exist in the installed versions.
clean = read_csv("data.csv")
client = openai.OpenAI()

# Method call on an inferred-type receiver -> needs type inference, out of scope.
merged = df.merge(clean, on="id")

# Var-keyword target (`requests.get(url, **kwargs)`) -> kwargs unverifiable, silent.
requests.get("https://example.com", timeout=5, verify=False)
