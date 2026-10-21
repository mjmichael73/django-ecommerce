#!/usr/bin/env python3
"""Standalone validator for the django-ecommerce Kubernetes manifests (card a9cea652).

Django-free: it only parses YAML with ``yaml.safe_load`` and asserts the
structural invariants required by the brief. Mirrors the Grafana dashboard
validator pattern (``docker/grafana/scripts/validate_dashboards.py``).

Run locally:

    python3 scripts/validate_k8s_manifests.py

Run inside a throwaway container (CI / bind-mount, since the web image ships
only ``be/ecommerce`` and has no ``k8s/``):

    docker run --rm -v "$PWD":/mnt -w /mnt python:3.12-slim sh -c \\
      'python3 -m pip install --quiet pyyaml && python3 scripts/validate_k8s_manifests.py'

Exits 0 if all checks pass, 1 otherwise. This is the documented MANUAL gate;
``kubectl apply --dry-run=client -k k8s/overlays/prod`` is the human pre-flight
(check docs/kubernetes.md). CI never requires kubectl to exist.
"""
from __future__ import annotations

import sys
from pathlib import Path

import yaml


def _repo_root() -> Path:
    """Find the repo root by walking up from this file's location."""
    here = Path(__file__).resolve()
    for candidate in (here, *here.parents):
        if (candidate / "k8s").is_dir() and (candidate / "docker-compose.yml").is_file():
            return candidate
    # Fall back to "k8s/ exists above this script".
    for candidate in (here.parent, *here.parents):
        if (candidate / "k8s").is_dir():
            return candidate
    raise SystemExit("ERROR: could not locate repository root (no k8s/ directory found).")


REPO_ROOT = _repo_root()
K8S_DIR = REPO_ROOT / "k8s"
SCRIPTS_DIR = REPO_ROOT / "scripts"
DOCS_DIR = REPO_ROOT / "docs"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _failures() -> list[str]:
    """Collect failure messages; returned to the caller for a single exit."""
    fails: list[str] = []

    def check(condition: bool, message: str) -> None:
        if not condition:
            fails.append(message)

    def parse_file(rel: str) -> list[dict]:
        """Load all YAML documents from k8s/<rel>; non-dict docs and missing
        files are recorded as failures."""
        path = K8S_DIR / rel
        if not path.is_file():
            check(False, f"missing file: k8s/{rel}")
            return []
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:  # pragma: no cover - IO error
            check(False, f"cannot read k8s/{rel}: {exc}")
            return []
        try:
            docs = list(yaml.safe_load_all(text))
        except yaml.YAMLError as exc:
            check(False, f"invalid YAML in k8s/{rel}: {exc}")
            return []
        out: list[dict] = []
        for i, doc in enumerate(docs):
            if doc is None:
                continue  # blank document separator
            if not isinstance(doc, dict):
                check(False, f"k8s/{rel}: document {i} is not a mapping (got {type(doc).__name__})")
                continue
            out.append(doc)
        return out

    # ------------------------------------------------------------------
    # 1. base resources parse + every kustomization references existing files
    # ------------------------------------------------------------------
    base_names = [
        "configmap.yaml",
        "secret.yaml",
        "deployment-web.yaml",
        "deployment-worker.yaml",
        "deployment-beat.yaml",
        "service-web.yaml",
        "kustomization.yaml",
    ]
    for name in base_names:
        docs = parse_file(f"base/{name}")
        if name.endswith("kustomization.yaml"):
            if docs:
                check(docs[0], f"kustomization.base/{name} did not parse")

    # base kustomization references
    base_kustomize = parse_file("base/kustomization.yaml")
    if base_kustomize:
        bk = base_kustomize[0]
        for res in bk.get("resources", []):
            p = K8S_DIR / "base" / res
            check(p.is_file(), f"base/kustomization.yaml references missing resource: {res}")
        for img in bk.get("images", []):
            check(
                img.get("name", "").startswith("ghcr.io/mjmichael73/django-ecommerce"),
                f"base image anchor must be the CI-published GHCR name (got {img.get('name')!r})",
            )

    # overlays reference ../base
    for ov in ("dev", "prod"):
        ok = parse_file(f"overlays/{ov}/kustomization.yaml")
        if ok:
            ovk = ok[0]
            bases = ovk.get("bases", [])
            check(
                any(b in ("../base", "../../base", "base") for b in bases),
                f"overlays/{ov}/kustomization.yaml must reference ../base (got {bases})",
            )
            # overlay kustomization references existing files in patches targets are names only;
            # verify each overlay has its own kustomization
            check(
                (K8S_DIR / "overlays" / ov / "kustomization.yaml").is_file(),
                f"missing overlays/{ov}/kustomization.yaml",
            )

    # ------------------------------------------------------------------
    # 2. Load the canonical resource documents by kind
    # ------------------------------------------------------------------
    def by_kind(docs: list[dict]) -> dict[str, dict]:
        return {d.get("kind"): d for d in docs}

    web_docs = by_kind(parse_file("base/deployment-web.yaml"))
    svc_docs = by_kind(parse_file("base/service-web.yaml"))
    cm_docs = by_kind(parse_file("base/configmap.yaml"))
    sec_docs = by_kind(parse_file("base/secret.yaml"))
    wk_docs = by_kind(parse_file("base/deployment-worker.yaml"))
    beat_docs = by_kind(parse_file("base/deployment-beat.yaml"))

    web_deploy = web_docs.get("Deployment")
    web_service = svc_docs.get("Service")
    configmap = cm_docs.get("ConfigMap")
    secret = sec_docs.get("Secret")
    worker_deploy = wk_docs.get("Deployment")
    beat_deploy = beat_docs.get("Deployment")

    check(web_deploy, "deployment-web.yaml: missing Deployment")
    check(web_service, "service-web.yaml: missing Service")
    check(configmap, "configmap.yaml: missing ConfigMap")
    check(secret, "secret.yaml: missing Secret")
    check(worker_deploy, "deployment-worker.yaml: missing Deployment")
    check(beat_deploy, "deployment-beat.yaml: missing Deployment")

    # ------------------------------------------------------------------
    # 3. web Deployment: exactly ONE Service whose selector matches pod labels
    # ------------------------------------------------------------------
    if web_deploy and web_service:
        pod_labels = (
            web_deploy.get("spec", {})
            .get("template", {})
            .get("metadata", {})
            .get("labels", {})
        )
        selector = web_service.get("spec", {}).get("selector", {})
        check(
            bool(pod_labels) and bool(selector),
            "web Deployment must define pod labels and Service must define a selector",
        )
        if pod_labels and selector:
            # Every key in the Service selector must appear in pod labels with the same value.
            for k, v in selector.items():
                check(
                    pod_labels.get(k) == v,
                    f"Service selector {k}={v!r} does not match web pod label {k}={pod_labels.get(k)!r}",
                )
    check(
        len(svc_docs) == 1,
        f"expected exactly one Service among web resources (got {len(svc_docs)})",
    )

    # ------------------------------------------------------------------
    # 4. web has BOTH probes (readiness /health/, liveness non-DB) on port 8000
    # ------------------------------------------------------------------
    if web_deploy:
        c0 = (
            web_deploy.get("spec", {})
            .get("template", {})
            .get("spec", {})
            .get("containers", [{}])[0]
        )
        readiness = c0.get("readinessProbe", {})
        liveness = c0.get("livenessProbe", {})
        http_get = readiness.get("httpGet", {})
        check(
            http_get.get("path") == "/health/" and http_get.get("port") == 8000,
            f"web readinessProbe must be httpGet /health/ on 8000 (got {http_get})",
        )
        tcp = liveness.get("tcpSocket", {})
        check(
            tcp.get("port") == 8000,
            f"web livenessProbe must be tcpSocket 8000 (non-DB; got {liveness})",
        )

    # ------------------------------------------------------------------
    # 5. web + worker + beat all consume BOTH ConfigMap and Secret via envFrom
    # ------------------------------------------------------------------
    def envfrom_names(deploy: dict | None) -> list[str]:
        if not deploy:
            return []
        refs: list[str] = []
        for c in deploy.get("spec", {}).get("template", {}).get("spec", {}).get("containers", []):
            for ef in c.get("envFrom", []):
                refs.append(ef.get("configMapRef", {}).get("name", ""))
                refs.append(ef.get("secretRef", {}).get("name", ""))
        return refs

    for name, deploy, want_cm, want_secret in (
        ("web", web_deploy, "ecommerce-config", "ecommerce-secrets"),
        ("worker", worker_deploy, "ecommerce-config", "ecommerce-secrets"),
        ("beat", beat_deploy, "ecommerce-config", "ecommerce-secrets"),
    ):
        refs = envfrom_names(deploy)
        check(want_cm in refs, f"{name} Deployment must envFrom the ConfigMap ({want_cm}); got {refs}")
        check(want_secret in refs, f"{name} Deployment must envFrom the Secret ({want_secret}); got {refs}")

    # ------------------------------------------------------------------
    # 6. beat + worker command overrides differ from web's gunicorn CMD
    # ------------------------------------------------------------------
    def cmd_of(deploy: dict | None) -> list[str]:
        if not deploy:
            return []
        containers = deploy.get("spec", {}).get("template", {}).get("spec", {}).get("containers", [])
        if containers:
            c = containers[0]
            return list(c.get("command", [])) or list(c.get("args", []))
        return []

    web_cmd = cmd_of(web_deploy)
    wk_cmd = cmd_of(worker_deploy)
    beat_cmd = cmd_of(beat_deploy)

    # web uses gunicorn (the image CMD) — it should NOT have a command override.
    check(
        not web_cmd,
        f"web Deployment must use the image's gunicorn CMD (no command override); got {web_cmd}",
    )
    check(
        wk_cmd and "worker" in wk_cmd and "celery" in wk_cmd,
        f"worker Deployment command must run celery worker (got {wk_cmd})",
    )
    check(
        beat_cmd and "beat" in beat_cmd and "celery" in beat_cmd,
        f"beat Deployment command must run celery beat (got {beat_cmd})",
    )
    check(
        wk_cmd != beat_cmd,
        f"worker and beat command overrides must differ (worker={wk_cmd}, beat={beat_cmd})",
    )

    # ------------------------------------------------------------------
    # 7. DJANGO_BEHIND_PROXY=1 on web
    # ------------------------------------------------------------------
    if configmap:
        data = configmap.get("data", {})
        check(
            data.get("DJANGO_BEHIND_PROXY") == "1",
            f"ConfigMap must set DJANGO_BEHIND_PROXY=1 (got {data.get('DJANGO_BEHIND_PROXY')!r})",
        )

    # ------------------------------------------------------------------
    # 8. resource requests + limits on all containers
    # ------------------------------------------------------------------
    for name, deploy in (("web", web_deploy), ("worker", worker_deploy), ("beat", beat_deploy)):
        if deploy:
            for i, c in enumerate(
                deploy.get("spec", {}).get("template", {}).get("spec", {}).get("containers", [])
            ):
                res = c.get("resources", {})
                check(
                    res.get("requests") is not None,
                    f"{name} container[{i}] missing resources.requests",
                )
                check(
                    res.get("limits") is not None,
                    f"{name} container[{i}] missing resources.limits",
                )

    # ------------------------------------------------------------------
    # 9. Blue-green deployment (card 828ff8ea)
    # ------------------------------------------------------------------
    # 9a. base web Service selector includes `color` and resolves to exactly
    #     one color value (the shipped default is blue).
    if web_service and web_deploy:
        svc_selector = web_service.get("spec", {}).get("selector", {})
        svc_color = svc_selector.get("color")
        check(
            svc_color is not None,
            "service-web selector must include a `color` key for blue-green (card 828ff8ea)",
        )
        check(
            svc_color == "blue",
            f"service-web selector `color` must be the shipped default 'blue' (got {svc_color!r})",
        )

        # base web pod template labels must include color: blue
        pod_labels = (
            web_deploy.get("spec", {})
            .get("template", {})
            .get("metadata", {})
            .get("labels", {})
        )
        check(
            pod_labels.get("color") == "blue",
            f"base web pod template must label color: blue (got {pod_labels.get('color')!r})",
        )
        # deployment matchLabels must also carry the color so the green twin
        # (same app label) doesn't collide with the blue selector.
        match_labels = (
            web_deploy.get("spec", {}).get("selector", {}).get("matchLabels", {})
        )
        check(
            match_labels.get("color") == "blue",
            f"base web deployment selector.matchLabels must include color: blue (got {match_labels.get('color')!r})",
        )

    # 9b. green twin manifest structure
    green_docs = by_kind(parse_file("blue-green/deployment-web-green.yaml"))
    green_deploy = green_docs.get("Deployment")
    check(
        green_deploy is not None,
        "k8s/blue-green/deployment-web-green.yaml: missing Deployment",
    )
    if green_deploy:
        check(
            green_deploy.get("metadata", {}).get("name") == "ecommerce-web-green",
            f"green Deployment name must be 'ecommerce-web-green' (got {green_deploy.get('metadata', {}).get('name')!r})",
        )
        g_pod_labels = (
            green_deploy.get("spec", {})
            .get("template", {})
            .get("metadata", {})
            .get("labels", {})
        )
        check(
            g_pod_labels.get("color") == "green",
            f"green pod template labels must include color: green (got {g_pod_labels.get('color')!r})",
        )
        check(
            g_pod_labels.get("app") == "ecommerce-web",
            f"green pod template labels must keep app: ecommerce-web (got {g_pod_labels.get('app')!r})",
        )
        # both probes present on the green twin
        g_c0 = (
            green_deploy.get("spec", {})
            .get("template", {})
            .get("spec", {})
            .get("containers", [{}])[0]
        )
        g_ready = g_c0.get("readinessProbe", {})
        g_live = g_c0.get("livenessProbe", {})
        check(
            g_ready.get("httpGet", {}).get("path") == "/health/"
            and g_ready.get("httpGet", {}).get("port") == 8000,
            f"green Deployment readinessProbe must be httpGet /health/ on 8000 (got {g_ready.get('httpGet')})",
        )
        check(
            g_live.get("tcpSocket", {}).get("port") == 8000,
            f"green Deployment livenessProbe must be tcpSocket 8000 (got {g_live})",
        )
        # envFrom both config + secret
        g_refs = envfrom_names(green_deploy)
        check("ecommerce-config" in g_refs, f"green Deployment must envFrom ConfigMap (got {g_refs})")
        check("ecommerce-secrets" in g_refs, f"green Deployment must envFrom Secret (got {g_refs})")
        # port 8000 declared
        g_ports = [p.get("containerPort") for p in g_c0.get("ports", [])]
        check(8000 in g_ports, f"green Deployment must expose containerPort 8000 (got {g_ports})")

    # 9c. green Deployment is NOT in base kustomization (base stays single-colored)
    if base_kustomize:
        bk = base_kustomize[0]
        base_res = bk.get("resources", [])
        green_in_base = any("deployment-web-green" in str(r) for r in base_res)
        check(
            not green_in_base,
            "green twin must NOT live in base kustomization resources (base stays single-colored) — applied by path via scripts/blue_green_deploy.sh",
        )

    # 9d. helper script: ordered gate + single-color flip + flip only touches the
    #     Service selector.
    script_path = SCRIPTS_DIR / "blue_green_deploy.sh"
    if script_path.is_file():
        script_text = script_path.read_text(encoding="utf-8")
        # rollout status BEFORE the selector flip (cutover ordering) — checked
        # within cmd_cutover's body so doc comments / other functions don't
        # produce false positives.
        cutover_idx = script_text.find("cmd_cutover()")
        if cutover_idx != -1:
            cutover_body = script_text[cutover_idx:]
        else:
            cutover_body = script_text
        rollout_idx = cutover_body.find("kubectl rollout status")
        flip_idx = cutover_body.find("flip_selector green")
        check(
            rollout_idx != -1 and flip_idx != -1 and rollout_idx < flip_idx,
            "blue_green_deploy.sh: cutover MUST run `kubectl rollout status` BEFORE "
            "`flip_selector green` (wait for green pods to boot-migrate + pass "
            "/health/ before the traffic switch)",
        )
        # the patch target must be the svc, and only the color selector is set
        patch_lines = [
            ln for ln in script_text.splitlines()
            if 'patch "svc/' in ln or 'svc/$SERVICE_NAME' in ln
        ]
        check(
            bool(patch_lines),
            "blue_green_deploy.sh: must patch svc/ecommerce-web (the web Service)",
        )
        # only blue/green are allowed flip targets (matched as whole words, quoted
        # or bare — the script calls `flip_selector blue|green`)
        for allowed in ("blue", "green"):
            check(
                allowed in script_text,
                f"blue_green_deploy.sh: must be able to flip the selector to {allowed}",
            )
        # The flip MUST ONLY patch the Service selector.color — no other resource
        # and no other selector key. We assert:
        #  (a) the kubectl patch call targets svc/$SERVICE_NAME (a Service),
        #  (b) the JSON patch body sets ONLY spec.selector.color (the build line
        #      is python3 jq that embeds "color" but no other selector key),
        #  (c) no kubectl patch line targets a Deployment.
        patch_lines = [
            ln for ln in script_text.splitlines()
            if "kubectl patch" in ln
        ]
        # exclude pure comment lines from the negative checks
        patch_cmd_lines = [ln for ln in patch_lines if not ln.lstrip().startswith("#")]
        check(
            bool(patch_cmd_lines),
            "blue_green_deploy.sh: must contain a `kubectl patch` invocation for the selector flip",
        )
        # (a) targets the Service
        check(
            any("svc/$SERVICE_NAME" in ln or "svc/ecommerce-web" in ln for ln in patch_cmd_lines),
            "blue_green_deploy.sh: the selector patch must target svc/ecommerce-web (the Service)",
        )
        # (c) never targets a Deployment
        check(
            not any("deploy/" in ln for ln in patch_cmd_lines),
            "blue_green_deploy.sh: the selector patch must not target a Deployment",
        )
        # (b) the patch body sets ONLY color — verify the python3/jq builder lines
        #     embed "color" and do not embed any other selector key (app, etc.).
        body_lines = [
            ln for ln in script_text.splitlines()
            if "patch_json=" in ln and ("python3" in ln or "jq" in ln)
        ]
        check(
            bool(body_lines),
            "blue_green_deploy.sh: patch JSON must be built from color only",
        )
        if body_lines:
            body = "\n".join(body_lines)
            check(
                '"color"' in body or "'color'" in body or "color" in body,
                "blue_green_deploy.sh: patch body must flip spec.selector.color",
            )
            # the builder must not set app or any other selector key
            for other_key in ("app", "version", "tier", "env"):
                check(
                    f'"{other_key}"' not in body and f"'{other_key}'" not in body,
                    f"blue_green_deploy.sh: patch body must set ONLY color (found {other_key})",
                )
    else:
        check(False, "scripts/blue_green_deploy.sh must exist (card 828ff8ea)")

    # 9e. runbook documentation
    runbook_path = DOCS_DIR / "blue-green.md"
    check(runbook_path.is_file(), "docs/blue-green.md must exist (card 828ff8ea)")
    if runbook_path.is_file():
        rb = runbook_path.read_text(encoding="utf-8").lower()
        check(
            "flip" in rb or "selector" in rb,
            "docs/blue-green.md must document the Service selector flip",
        )

    return fails


def main() -> int:
    fails = _failures()
    if fails:
        print("FAIL: k8s manifest validation failed:")
        for f in fails:
            print(f"  - {f}")
        return 1
    print("OK: k8s manifests validated (base + dev/prod + green, web/worker/beat, envFrom, probes, commands, blue-green gate).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
