"""Static deployment checks for Favlist's trusted reverse-proxy boundaries."""

from pathlib import Path

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_compose_uses_fixed_internal_proxy_addresses() -> None:
    """Keep API trust scoped to web while exposing one configurable edge peer."""
    compose = yaml.safe_load((PROJECT_ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    services = compose["services"]
    assert services["api"]["environment"]["TRUSTED_PROXY_CIDRS"] == (
        "${TRUSTED_PROXY_CIDRS:-172.30.55.10/32}"
    )
    assert services["web"]["environment"]["TRUSTED_EDGE_PROXY"] == (
        "${TRUSTED_EDGE_PROXY:-172.30.55.1}"
    )
    assert services["web"]["networks"]["default"]["ipv4_address"] == "172.30.55.10"
    assert services["caddy"]["networks"]["default"]["ipv4_address"] == "172.30.55.30"
    assert compose["networks"]["default"]["ipam"]["config"][0]["subnet"] == (
        "172.30.55.0/24"
    )


def test_web_proxy_overwrites_forwarding_headers_from_trusted_edges() -> None:
    """Ensure untrusted callers cannot append a forged address through the web proxy."""
    nginx = (PROJECT_ROOT / "frontend" / "nginx.conf").read_text(encoding="utf-8")
    dockerfile = (PROJECT_ROOT / "frontend" / "Dockerfile").read_text(encoding="utf-8")
    dockerignore = (PROJECT_ROOT / "frontend" / ".dockerignore").read_text(encoding="utf-8")
    assert "set_real_ip_from 172.30.55.30;" in nginx
    assert "set_real_ip_from ${TRUSTED_EDGE_PROXY};" in nginx
    assert "root /usr/share/nginx/html;" in nginx
    assert "index index.html;" in nginx
    assert "proxy_set_header X-Forwarded-For $remote_addr;" in nginx
    assert "$proxy_add_x_forwarded_for" not in nginx
    assert "NGINX_ENVSUBST_FILTER=TRUSTED_EDGE_PROXY" in dockerfile
    assert "/etc/nginx/templates/default.conf.template" in dockerfile
    assert "node_modules/" in dockerignore
    assert "dist/" in dockerignore
    assert "*.tsbuildinfo" in dockerignore


def test_api_container_disables_automatic_proxy_header_parsing() -> None:
    """Leave forwarded-address parsing solely to the audited application boundary."""
    dockerfile = (PROJECT_ROOT / "backend" / "Dockerfile").read_text(encoding="utf-8")
    assert '"--no-proxy-headers"' in dockerfile
    assert '"--proxy-headers"' not in dockerfile
