"""Realistic LLM-generated code carrying version-drift API mistakes.

apidrift's anchor fixture and README demo. Above the divider: genuine symbol-existence
mistakes Check A flags against the installed packages — three distinct mechanisms an
LLM trained on an older API would emit. Below the divider: cases apidrift must stay
SILENT on. Soundness is the product, so the fixture tests it like a feature.

NOTE: not runnable; it is parsed and introspected, not executed.
"""

import openai
import pandas as pd
import requests
from pandas import read_csv

# (1) Typo / hallucination — there is no `pandas.read_exel`.
frame = pd.read_exel("sheet.xlsx")

# (2) Cross-library confusion — `concatenate` is numpy; pandas spells it `concat`.
combined = pd.concatenate([frame, frame])

# (3) Version removal — `TimeGrouper` was removed in pandas 1.0 (use `Grouper`).
grouper = pd.TimeGrouper("M")

# ----------------------------------------------------------------------------- #
# Everything below must stay SILENT.
# ----------------------------------------------------------------------------- #

# Deprecation shim, NOT a missing symbol: openai 1.x keeps `ChatCompletion` as a
# proxy that exists on attribute access and only raises when *called*. Existence
# introspection sees it as present, so Check A correctly says nothing — that drift
# is a call-time/deprecation concern, a different (harder) check than existence.
response = openai.ChatCompletion.create(model="gpt-4", messages=[])

# `read_csv` exists; the removed `mangle_dupe_cols` keyword is a Check B (kwargs)
# problem, not an existence one — Check A stays silent here by design.
table = pd.read_csv("data.csv", mangle_dupe_cols=True)

# Valid calls that genuinely exist in the installed versions.
clean = read_csv("data.csv")
client = openai.OpenAI()

# Method call on an inferred-type receiver -> needs type inference, out of scope.
merged = clean.merge(frame, on="id")

# Var-keyword target (`requests.get(url, **kwargs)`) -> kwargs unverifiable, silent.
requests.get("https://example.com", timeout=5, verify=False)
