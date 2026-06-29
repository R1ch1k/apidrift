"""Benchmark-validation regression set — real version-drift cases, empirically pinned.

Each case is a genuine, documented API change (a symbol removed/renamed, or a keyword
argument dropped in a later major version) in a pip-installable library, drawn from the
release-note history of the seed libraries — the same version-drift classes catalogued by
public benchmarks such as GitChameleon, VersiCode, and CodeUpdateArena. Every case was
verified empirically against the pinned installed versions before being admitted; any
candidate that could not be made to flag deterministically (e.g. pydantic's ``Field``,
which accepts ``**kwargs``, or ``BaseSettings``, which sits behind a module ``__getattr__``)
was dropped rather than fudged.

For every case we assert BOTH directions, which is the whole product:

* the **drifted** call (valid in an older version, gone in the installed one) is FLAGGED
  with exactly one finding of the expected kind;
* the **corresponding correct** call — the modern replacement — stays completely SILENT.

The set is pinned to the versions it was validated against (see ``_PINNED``); on any other
environment the module skips wholesale rather than report a misleading count. The total
here is the *only* number allowed to fill the README "validated against N" line.
"""

from __future__ import annotations

import importlib.metadata
from dataclasses import dataclass

import pytest

from apidrift import resolver
from apidrift.checks import Severity, Violation, check_call

# Versions the cases below were empirically validated against. Install with the ``bench``
# optional-dependency group (`pip install -e .[bench]`) to reproduce the exact count.
_PINNED = {"pandas": "2.3.3", "scikit-learn": "1.8.0"}


def _version_or_none(dist: str) -> str | None:
    try:
        return importlib.metadata.version(dist)
    except importlib.metadata.PackageNotFoundError:
        return None


_PINS_OK = all(_version_or_none(dist) == want for dist, want in _PINNED.items())

pytestmark = pytest.mark.skipif(
    not _PINS_OK,
    reason=(
        "benchmark validated against pinned "
        + ", ".join(f"{d}=={v}" for d, v in _PINNED.items())
        + " — install the 'bench' extra to run it"
    ),
)


@dataclass(frozen=True)
class DriftCase:
    """One real version-drift case: a drifted call that must flag, and its modern fix."""

    case_id: str
    dist: str  # distribution the case lives in (documentation only)
    kind: str  # expected finding: "existence" | "keyword"
    token: str  # the offending leaf — missing segment, or removed keyword
    drifted: str  # source that MUST be flagged (one finding of `kind`)
    correct: str  # source that MUST stay silent (the modern replacement)
    note: str  # provenance: what changed, in which release


CASES: tuple[DriftCase, ...] = (
    # ------------------------------------------------------------------ #
    # pandas — symbols removed / renamed at the top level
    # ------------------------------------------------------------------ #
    DriftCase(
        "pd_timegrouper", "pandas", "existence", "TimeGrouper",
        "import pandas as pd\npd.TimeGrouper('M')\n",
        "import pandas as pd\npd.Grouper(freq='M')\n",
        "TimeGrouper removed in pandas 1.0 (use Grouper)",
    ),
    DriftCase(
        "pd_scatter_matrix", "pandas", "existence", "scatter_matrix",
        "import pandas as pd\npd.scatter_matrix(None)\n",
        "import pandas as pd\npd.plotting.scatter_matrix(None)\n",
        "scatter_matrix moved to pandas.plotting in 0.20, removed from top level in 0.23",
    ),
    DriftCase(
        "pd_sparse_dataframe", "pandas", "existence", "SparseDataFrame",
        "import pandas as pd\npd.SparseDataFrame()\n",
        "import pandas as pd\npd.DataFrame()\n",
        "SparseDataFrame removed in pandas 1.0 (use a DataFrame with sparse dtypes)",
    ),
    DriftCase(
        "pd_timeseries", "pandas", "existence", "TimeSeries",
        "import pandas as pd\npd.TimeSeries([])\n",
        "import pandas as pd\npd.Series([])\n",
        "TimeSeries alias removed in pandas 0.20 (use Series)",
    ),
    DriftCase(
        "pd_get_store", "pandas", "existence", "get_store",
        "import pandas as pd\npd.get_store('x')\n",
        "import pandas as pd\npd.HDFStore('x')\n",
        "get_store removed in pandas 0.21 (use HDFStore)",
    ),
    DriftCase(
        "pd_panel", "pandas", "existence", "Panel",
        "import pandas as pd\npd.Panel()\n",
        "import pandas as pd\npd.DataFrame()\n",
        "Panel removed in pandas 0.25 (use DataFrame / MultiIndex)",
    ),
    # ------------------------------------------------------------------ #
    # pandas — keyword arguments removed (mostly the 2.0 cull)
    # ------------------------------------------------------------------ #
    DriftCase(
        "pd_read_csv_mangle_dupe_cols", "pandas", "keyword", "mangle_dupe_cols",
        "import pandas as pd\npd.read_csv('x', mangle_dupe_cols=True)\n",
        "import pandas as pd\npd.read_csv('x', sep=',')\n",
        "read_csv(mangle_dupe_cols=) removed in pandas 2.0",
    ),
    DriftCase(
        "pd_read_csv_error_bad_lines", "pandas", "keyword", "error_bad_lines",
        "import pandas as pd\npd.read_csv('x', error_bad_lines=False)\n",
        "import pandas as pd\npd.read_csv('x', on_bad_lines='skip')\n",
        "read_csv(error_bad_lines=) removed in pandas 2.0 (use on_bad_lines)",
    ),
    DriftCase(
        "pd_read_csv_warn_bad_lines", "pandas", "keyword", "warn_bad_lines",
        "import pandas as pd\npd.read_csv('x', warn_bad_lines=True)\n",
        "import pandas as pd\npd.read_csv('x', on_bad_lines='warn')\n",
        "read_csv(warn_bad_lines=) removed in pandas 2.0 (use on_bad_lines)",
    ),
    DriftCase(
        "pd_read_csv_squeeze", "pandas", "keyword", "squeeze",
        "import pandas as pd\npd.read_csv('x', squeeze=True)\n",
        "import pandas as pd\npd.read_csv('x', sep=',')\n",
        "read_csv(squeeze=) removed in pandas 2.0 (call .squeeze() instead)",
    ),
    DriftCase(
        "pd_read_csv_prefix", "pandas", "keyword", "prefix",
        "import pandas as pd\npd.read_csv('x', prefix='c')\n",
        "import pandas as pd\npd.read_csv('x', sep=',')\n",
        "read_csv(prefix=) removed in pandas 2.0",
    ),
    DriftCase(
        "pd_concat_join_axes", "pandas", "keyword", "join_axes",
        "import pandas as pd\npd.concat([], join_axes=[])\n",
        "import pandas as pd\npd.concat([], axis=1)\n",
        "concat(join_axes=) removed in pandas 1.0 (reindex afterwards)",
    ),
    DriftCase(
        "pd_read_excel_convert_float", "pandas", "keyword", "convert_float",
        "import pandas as pd\npd.read_excel('x', convert_float=True)\n",
        "import pandas as pd\npd.read_excel('x', sheet_name=0)\n",
        "read_excel(convert_float=) removed in pandas 2.0",
    ),
    DriftCase(
        "pd_read_json_numpy", "pandas", "keyword", "numpy",
        "import pandas as pd\npd.read_json('x', numpy=True)\n",
        "import pandas as pd\npd.read_json('x')\n",
        "read_json(numpy=) removed in pandas 2.0",
    ),
    # ------------------------------------------------------------------ #
    # scikit-learn — estimator keyword arguments removed / renamed
    # ------------------------------------------------------------------ #
    DriftCase(
        "skl_logreg_multi_class", "scikit-learn", "keyword", "multi_class",
        "from sklearn.linear_model import LogisticRegression\n"
        "LogisticRegression(multi_class='ovr')\n",
        "from sklearn.linear_model import LogisticRegression\n"
        "LogisticRegression(max_iter=1000)\n",
        "LogisticRegression(multi_class=) removed in scikit-learn 1.7",
    ),
    DriftCase(
        "skl_kmeans_n_jobs", "scikit-learn", "keyword", "n_jobs",
        "from sklearn.cluster import KMeans\nKMeans(n_jobs=2)\n",
        "from sklearn.cluster import KMeans\nKMeans(n_clusters=3)\n",
        "KMeans(n_jobs=) removed in scikit-learn 1.0",
    ),
    DriftCase(
        "skl_kmeans_precompute_distances", "scikit-learn", "keyword", "precompute_distances",
        "from sklearn.cluster import KMeans\nKMeans(precompute_distances=True)\n",
        "from sklearn.cluster import KMeans\nKMeans(n_clusters=3)\n",
        "KMeans(precompute_distances=) removed in scikit-learn 1.0",
    ),
    DriftCase(
        "skl_sgd_n_iter", "scikit-learn", "keyword", "n_iter",
        "from sklearn.linear_model import SGDClassifier\nSGDClassifier(n_iter=5)\n",
        "from sklearn.linear_model import SGDClassifier\nSGDClassifier(max_iter=1000)\n",
        "SGDClassifier(n_iter=) removed in scikit-learn 0.21 (use max_iter)",
    ),
    DriftCase(
        "skl_ridge_normalize", "scikit-learn", "keyword", "normalize",
        "from sklearn.linear_model import Ridge\nRidge(normalize=True)\n",
        "from sklearn.linear_model import Ridge\nRidge(alpha=1.0)\n",
        "Ridge(normalize=) removed in scikit-learn 1.2 (use a StandardScaler)",
    ),
    DriftCase(
        "skl_linreg_normalize", "scikit-learn", "keyword", "normalize",
        "from sklearn.linear_model import LinearRegression\nLinearRegression(normalize=True)\n",
        "from sklearn.linear_model import LinearRegression\nLinearRegression(fit_intercept=True)\n",
        "LinearRegression(normalize=) removed in scikit-learn 1.2 (use a StandardScaler)",
    ),
    DriftCase(
        "skl_ohe_categorical_features", "scikit-learn", "keyword", "categorical_features",
        "from sklearn.preprocessing import OneHotEncoder\n"
        "OneHotEncoder(categorical_features=[0])\n",
        "from sklearn.preprocessing import OneHotEncoder\n"
        "OneHotEncoder(handle_unknown='ignore')\n",
        "OneHotEncoder(categorical_features=) removed in scikit-learn 0.22 (use ColumnTransformer)",
    ),
    DriftCase(
        "skl_ohe_n_values", "scikit-learn", "keyword", "n_values",
        "from sklearn.preprocessing import OneHotEncoder\nOneHotEncoder(n_values=3)\n",
        "from sklearn.preprocessing import OneHotEncoder\nOneHotEncoder(categories='auto')\n",
        "OneHotEncoder(n_values=) removed in scikit-learn 0.22 (use categories)",
    ),
    DriftCase(
        "skl_dtree_presort", "scikit-learn", "keyword", "presort",
        "from sklearn.tree import DecisionTreeClassifier\nDecisionTreeClassifier(presort=True)\n",
        "from sklearn.tree import DecisionTreeClassifier\nDecisionTreeClassifier(max_depth=3)\n",
        "DecisionTreeClassifier(presort=) removed in scikit-learn 0.24",
    ),
    DriftCase(
        "skl_rf_min_impurity_split", "scikit-learn", "keyword", "min_impurity_split",
        "from sklearn.ensemble import RandomForestClassifier\n"
        "RandomForestClassifier(min_impurity_split=0.1)\n",
        "from sklearn.ensemble import RandomForestClassifier\n"
        "RandomForestClassifier(n_estimators=10)\n",
        "RandomForestClassifier(min_impurity_split=) removed in scikit-learn 1.0",
    ),
    DriftCase(
        "skl_tsne_n_iter", "scikit-learn", "keyword", "n_iter",
        "from sklearn.manifold import TSNE\nTSNE(n_iter=250)\n",
        "from sklearn.manifold import TSNE\nTSNE(n_components=2)\n",
        "TSNE(n_iter=) removed in scikit-learn 1.7 (use max_iter)",
    ),
    # ------------------------------------------------------------------ #
    # scikit-learn — symbols removed / renamed / relocated
    # ------------------------------------------------------------------ #
    DriftCase(
        "skl_jaccard_similarity_score", "scikit-learn", "existence", "jaccard_similarity_score",
        "from sklearn.metrics import jaccard_similarity_score\njaccard_similarity_score(0, 0)\n",
        "from sklearn.metrics import jaccard_score\njaccard_score(0, 0)\n",
        "jaccard_similarity_score renamed jaccard_score, removed in scikit-learn 0.23",
    ),
    DriftCase(
        "skl_calinski_harabaz_score", "scikit-learn", "existence", "calinski_harabaz_score",
        "from sklearn.metrics import calinski_harabaz_score\ncalinski_harabaz_score(0, 0)\n",
        "from sklearn.metrics import calinski_harabasz_score\ncalinski_harabasz_score(0, 0)\n",
        "calinski_harabaz_score renamed calinski_harabasz_score, removed in scikit-learn 0.23",
    ),
    DriftCase(
        "skl_imputer", "scikit-learn", "existence", "Imputer",
        "from sklearn.preprocessing import Imputer\nImputer()\n",
        "from sklearn.impute import SimpleImputer\nSimpleImputer()\n",
        "preprocessing.Imputer moved to impute.SimpleImputer, removed in scikit-learn 0.22",
    ),
    DriftCase(
        "skl_randomized_lasso", "scikit-learn", "existence", "RandomizedLasso",
        "from sklearn.linear_model import RandomizedLasso\nRandomizedLasso()\n",
        "from sklearn.linear_model import Lasso\nLasso()\n",
        "RandomizedLasso removed in scikit-learn 0.21",
    ),
    DriftCase(
        "skl_randomized_logistic_regression", "scikit-learn", "existence",
        "RandomizedLogisticRegression",
        "from sklearn.linear_model import RandomizedLogisticRegression\n"
        "RandomizedLogisticRegression()\n",
        "from sklearn.linear_model import LogisticRegression\nLogisticRegression()\n",
        "RandomizedLogisticRegression removed in scikit-learn 0.21",
    ),
)


def _violations(source: str) -> list[Violation]:
    resolution = resolver.resolve_source(source)
    return [v for call in resolution.resolved for v in check_call(call)]


@pytest.mark.parametrize("case", CASES, ids=[c.case_id for c in CASES])
def test_drifted_call_is_flagged(case: DriftCase) -> None:
    """The drifted call produces exactly one finding of the expected kind."""
    violations = _violations(case.drifted)
    assert len(violations) == 1, f"{case.case_id}: expected exactly one finding, got {violations}"
    (violation,) = violations
    assert violation.check == case.kind
    assert violation.severity is Severity.ERROR
    assert violation.token == case.token


@pytest.mark.parametrize("case", CASES, ids=[c.case_id for c in CASES])
def test_correct_call_is_silent(case: DriftCase) -> None:
    """The modern replacement call must emit nothing — no false alarm on valid code."""
    assert _violations(case.correct) == [], case.case_id


def test_case_set_is_unique_and_counted() -> None:
    """The README "validated against N" line is tied to this count; keep it honest."""
    assert len({c.case_id for c in CASES}) == len(CASES)
    assert len(CASES) == 30
