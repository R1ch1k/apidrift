# Security & threat model

apidrift is a deterministic, $0 CI guard. To check whether the API calls in your code still
exist in the versions of your dependencies that are actually installed, it has to **import and
introspect those packages**. This document states plainly what that does and does not protect
against, so you know exactly where apidrift's boundary is.

## What apidrift introspects

apidrift only introspects packages that are **already installed in your environment** —
packages you chose to install, and whose code already runs whenever your own application
imports them. apidrift does not download, fetch, or resolve anything from the network, and it
never installs anything. Running apidrift over your code imports the same packages your code
imports; it adds no dependency surface you did not already have.

## How apidrift isolates that import

Importing a third-party package runs arbitrary code, so apidrift never imports a target package
in its own process. Every import + introspection runs in a short-lived **subprocess worker**,
one per root package, hardened to be robust against a package that misbehaves on import:

- a **wall-clock timeout** — a package that hangs is killed and treated as `unverifiable`
  (silent), never a hung run;
- a **`BaseException` catch** inside the worker — a `sys.exit()` / `os._exit()` on import
  degrades that one package to `unverifiable` instead of taking the run down;
- **stdout and stderr routed to the null device** — an import-time `print` or warning can never
  leak into apidrift's output (so `--json` stays a single clean document);
- **a hardened startup that closes the working-directory shadow vector** — the worker is
  launched with `-S` (no `site` / `sitecustomize` / `.pth` auto-run), from a freshly-created
  **empty working directory**, with `PYTHONSAFEPATH=1`, and on only the path needed to import
  apidrift itself. `python -m` would otherwise put the current directory first on `sys.path`
  (and `-S` does not remove it), so a file in the directory apidrift is run from — for the
  GitHub Action, the checked-out repo it scans — that shadows a stdlib name the worker imports
  (e.g. a `json.py`) could execute during startup. The empty cwd closes this on every supported
  version; `PYTHONSAFEPATH` drops that automatic path entry entirely on 3.11+. The worker then
  pins `sys.path` from the request for faithful resolution. This hardens one concrete startup
  vector — it is not a sandbox against a deliberately hostile package (see below);
- the result is serialized and written through references the worker **binds before importing**
  any target package, so ordinary import-time monkeypatching of the serialization path does not
  silently corrupt a verdict.

Whatever a package does on import — crash, hang, exit, or spew — the parent observes only a
clean result or a failure, and any failure becomes `unverifiable`, which the checks treat as
silence. This is what keeps apidrift's first tenet intact: **silence beats a false alarm.**

## What apidrift is NOT

apidrift is **not a sandbox for analyzing actively-malicious or untrusted packages.** The
isolation above exists to be robust against packages that *crash, hang, exit, or print* on
import — the things that happen by accident in a real dependency tree — not to defend against a
package deliberately attacking apidrift.

A package crafted to tamper with the worker at import time could, in principle, forge an
introspection result. apidrift deliberately does **not** chase that with ever-deeper defensive
binds: it is an unwinnable asymptote against code running in the same interpreter. The honest
boundary is this — **a package malicious enough to forge apidrift's worker at import time is
already executing arbitrary code in your environment**, the moment it is imported, by apidrift
or by your own application. Defending the verdict against such a package is therefore outside
apidrift's purpose: if you are running untrusted packages, the exposure is the install, not the
introspection.

Full sandbox isolation against hostile packages (e.g. a restricted, capability-limited execution
environment for the worker) is possible **future** hardening. It is not a guarantee apidrift
makes today, and it is not a launch gate.

## Known limitations

- **Editable installs.** Packages installed via editable installs (`pip install -e`) or `.pth`
  import hooks may be reported as `unverifiable` rather than checked, because the isolated worker
  uses a hardened startup (`-S`) that does not replay those `.pth`-based hooks. This is sound
  silence — such packages are never falsely flagged — but they are not checked.

## Reporting

If you find a soundness bug — in particular a **false positive** (apidrift flags valid code) or
a crash on a normal package — please open an issue. Soundness is the product; a missed drift is
tolerable, a false alarm is not.
