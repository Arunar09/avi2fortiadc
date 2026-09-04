"""
transformers/pool.py
Avi Pool → FortiADC Real Server Pool + Real Servers.
"""
from __future__ import annotations
from core.base import BaseTransformer
from core.events import Phase
from transformers.mappings import map_lb_algorithm, FORTIADC_PATHS, DEFAULTS


class PoolTransformer(BaseTransformer):
    object_type   = "pool"
    fortiadc_path = FORTIADC_PATHS["real_server_pool"]

    def transform(self, avi_pool: dict) -> dict | None:
        name = avi_pool.get("name", "?")
        algo, is_approx = map_lb_algorithm(avi_pool.get("lb_algorithm", ""))

        if is_approx:
            self._bus.warn(
                Phase.TRANSFORM,
                f"Pool '{name}': LB algorithm '{avi_pool.get('lb_algorithm')}' "
                f"mapped approximately to FortiADC '{algo}' — verify behaviour",
                object_type="pool", object_name=name,
                detail={"avi_algo": avi_pool.get("lb_algorithm"),
                        "fortiadc_algo": algo,
                        "action": "Test load distribution matches expectations."},
            )

        members = avi_pool.get("servers", [])
        simplified_members = []
        real_servers = []
        pool_members = []
        for s in members:
            ip = s.get("ip", {}).get("addr", "")
            port = s.get("port", 80)
            try:
                port = int(port)
            except Exception:
                port = 80

            wt_raw = s.get("ratio", 1)
            try:
                wt_int = int(wt_raw)
            except Exception:
                wt_int = 1
            if wt_int < 1:
                wt_int = 1

            simplified_members.append({
                "ip": ip,
                "port": port,
                "weight": wt_int,
            })
            rs_name = f"{name}-{ip.replace('.', '-')}-{port}"
            real_servers.append({
                "fortiadc_path": FORTIADC_PATHS["real_server"],
                "name": rs_name,
                "payload": {
                    "mkey":    rs_name,
                    "address": ip,
                    "port":    str(port),
                    "status":  "enable" if s.get("enabled", True) else "disable",
                    "weight":  str(wt_int),
                },
                "sub_objects": []
            })
            pool_members.append({
                "fortiadc_path": f"{self.fortiadc_path}/{name}/member",
                "name": rs_name,
                "payload": {
                    "mkey": rs_name,
                    "real-server": rs_name,
                    "port": str(port),
                    "weight": str(wt_int),
                    "status": "enable" if s.get("enabled", True) else "disable",
                },
                "depends_on": [(FORTIADC_PATHS["real_server"], rs_name)],
            })

        pool_payload = {
            "mkey":       name,
            "type":       "static",
            "lb-method":  algo,
            "health-check-inherit": "enable",
            "health-check": avi_pool.get("_health_monitor_name", ""),
        }

        return {
            "fortiadc_path": self.fortiadc_path,
            "name": name,
            "lb_method": algo,
            "members": simplified_members,
            "payload": pool_payload,
            "real_servers": real_servers,
            "pool_members": pool_members,
            "sub_objects": [],
            "depends_on": [],
        }


"""transformers/health_check.py"""
from core.base import BaseTransformer
from core.events import Phase
from transformers.mappings import map_health_monitor_type, FORTIADC_PATHS, DEFAULTS


class HealthCheckTransformer(BaseTransformer):
    object_type   = "healthmonitor"
    fortiadc_path = FORTIADC_PATHS["health_check"]

    def transform(self, avi_hm: dict) -> dict | None:
        name    = avi_hm.get("name", "?")
        avi_type = avi_hm.get("type", "")
        fadc_type = map_health_monitor_type(avi_type)

        if fadc_type is None:
            self._flag_unsupported(
                avi_hm,
                reason=f"Health monitor type '{avi_type}' has no FortiADC equivalent",
                suggestion="Replace with HTTP health check against application /health endpoint.",
            )
            return None

        interval_int = int(avi_hm.get("send_interval", DEFAULTS["health_check_interval"]))
        timeout_int = int(avi_hm.get("receive_timeout", DEFAULTS["health_check_timeout"]))

        # FortiADC requirement: timeout must be strictly less than interval.
        if timeout_int >= interval_int:
            self._flag_unsupported(
                avi_hm,
                reason="Health monitor timeout must be < interval for FortiADC",
                suggestion="Adjust send_interval / receive_timeout in the export.",
            )
            return None

        payload: dict = {
            "mkey":         name,
            "type":         fadc_type,
            "interval":     str(interval_int),
            "timeout":      str(timeout_int),
            "up-retry":     str(avi_hm.get("successful_checks", DEFAULTS["health_check_up_retry"])),
            "down-retry":   str(avi_hm.get("failed_checks",     DEFAULTS["health_check_down_retry"])),
        }

        if fadc_type in ("HTTP", "HTTPS"):
            http = avi_hm.get("http_monitor", {})
            payload["http-request-string"] = http.get("http_request",
                                                       "HEAD / HTTP/1.0\\r\\n\\r\\n")
            codes = http.get("http_response_code", [{"code": "HTTP_2XX"}])
            first_code = codes[0] if codes else {"code": "HTTP_2XX"}
            if isinstance(first_code, dict):
                code_str = first_code.get("code", "200")
            else:
                code_str = str(first_code)
            payload["http-expect-code"] = (
                "200" if "2XX" in code_str
                else "404" if "4XX" in code_str
                else code_str.replace("HTTP_", "")
            )
            if http.get("ssl_attributes"):
                payload["verify-server-cert"] = "enable"

        return {
            "fortiadc_path": self.fortiadc_path,
            "name": name,
            "type": fadc_type,
            "interval": interval_int,
            "timeout": timeout_int,
            "payload": payload,
            "depends_on": [],
        }


"""transformers/ssl.py"""
from core.base import BaseTransformer
from core.events import Phase
from transformers.mappings import FORTIADC_PATHS, TLS_DEPRECATED


class SSLCertTransformer(BaseTransformer):
    object_type   = "sslkeyandcertificate"
    fortiadc_path = FORTIADC_PATHS["ssl_cert"]

    def transform(self, avi_cert: dict) -> dict | None:
        name = avi_cert.get("name", "?")
        if not avi_cert.get("_exportable", True):
            self._flag_unsupported(
                avi_cert,
                reason="HSM-backed certificate — private key not exportable from Avi",
                suggestion="Issue new certificate from CA and import directly to FortiADC.",
            )
            return None

        cert_body = avi_cert.get("certificate", {}).get("certificate", "")
        key_body  = avi_cert.get("key", "")

        if not cert_body:
            self._bus.error(
                Phase.TRANSFORM,
                f"Certificate '{name}' has no PEM body in API response — "
                f"export manually from Avi UI",
                object_type="sslkeyandcertificate", object_name=name,
            )
            return None

        return {
            "fortiadc_path": self.fortiadc_path,
            "name": name,
            "payload": {
                "mkey":        name,
                "type":        "certificate",
                "certificate": cert_body,
                "private-key": key_body,
            },
            "depends_on": [],
            "_import_method": "api" if key_body else "manual_required",
        }


"""transformers/virtual_server.py"""
from core.base import BaseTransformer
from core.events import Phase
from transformers.mappings import FORTIADC_PATHS


class VirtualServerTransformer(BaseTransformer):
    object_type   = "virtualservice"
    fortiadc_path = FORTIADC_PATHS["virtual_server"]

    def transform(self, avi_vs: dict) -> dict | None:
        name = avi_vs.get("name", "?")

        # DataScript = always MANUAL, skip auto-transform
        if avi_vs.get("_has_datascripts"):
            self._flag_unsupported(
                avi_vs,
                reason="Virtual Service has DataScript(s) — cannot auto-migrate",
                suggestion="Resolve DataScript manual migration first, "
                           "then re-run transform.",
            )
            return None

        vips  = avi_vs.get("_vips", [])
        if not vips and avi_vs.get("_resolved", {}).get("_vsvip_obj"):
            vsvip = avi_vs["_resolved"]["_vsvip_obj"]
            vip_entries = vsvip.get("vip", [])
            if vip_entries:
                vips = [vip_entries[0].get("ip_address", {}).get("addr", "no-addr")]
                self._bus.info(Phase.TRANSFORM, f"VS '{name}': Resolved VIP {vips[0]} via vsvip_ref", object_name=name)

        if not vips:
            self._bus.error(
                Phase.TRANSFORM,
                f"VS '{name}' has no VIP address — check Avi VSVIP object",
                object_type="virtualservice", object_name=name,
            )
            return None

        vip  = vips[0]
        port = 80
        svcs = avi_vs.get("services", [])
        if svcs:
            port = svcs[0].get("port", 80)

        ssl_offload = bool(avi_vs.get("ssl_key_and_certificate_refs"))
        pool_name   = avi_vs.get("_resolved", {}).get("pool_ref", "")

        payload = {
            "mkey":          name,
            "addr-type":     "ipv4",
            "ip":            vip,
            "port":          str(port),
            "protocol":      "TCP",
            "status":        "enable" if avi_vs.get("enabled", True) else "disable",
            "pool":          pool_name,
            "ssl-mirror":    "enable" if ssl_offload else "disable",
            "client-ssl-profile": avi_vs.get("_resolved", {}).get("ssl_profile_ref", ""),
            "profile":       avi_vs.get("_resolved", {}).get("application_profile_ref", ""),
            "persistence":   pool_name + "-persist" if avi_vs.get("_resolved", {}).get(
                             "application_profile_ref") else "",
            "content-rewriting": avi_vs.get("_resolved", {}).get("http_policy_set_ref", ""),
        }

        certs = avi_vs.get("_resolved", {}).get("ssl_key_and_certificate_refs", [])
        if certs:
            payload["client-certificate"] = certs[0]

        return {
            "fortiadc_path": self.fortiadc_path,
            "name": name,
            "ip": vip,
            "port": port,
            "payload": {k: v for k, v in payload.items() if v},
            "depends_on": [
                (FORTIADC_PATHS["real_server_pool"], pool_name),
                *[(FORTIADC_PATHS["ssl_cert"], c) for c in certs],
            ],
        }
