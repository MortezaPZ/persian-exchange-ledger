"""Offline template contract checks, NOT nginx/systemd or TLS integration tests.

Run: python -m unittest discover -s deploy -p 'test_*.py' -v
No environment files, database connections, or external services are used.
"""
import configparser
import shlex
import unittest
from pathlib import Path

DEPLOY = Path(__file__).resolve().parent


class DeploymentTemplateTests(unittest.TestCase):
    def test_wsgi_listener_is_private_and_proxy_trust_is_explicit(self):
        unit = configparser.ConfigParser(interpolation=None, strict=False)
        unit.read(DEPLOY / "sarrafi.service", encoding="utf-8")
        command = shlex.split(unit["Service"]["ExecStart"])
        self.assertIn("--listen=127.0.0.1:8000", command)
        self.assertIn("--trusted-proxy=127.0.0.1", command)
        self.assertIn("--trusted-proxy-headers=x-forwarded-proto", command)
        self.assertEqual(command[-1], "config.wsgi:application")
        self.assertEqual(unit["Service"]["User"], "sarrafi")
        self.assertEqual(unit["Service"]["EnvironmentFile"], "/etc/sarrafi/sarrafi.env")
        self.assertEqual(unit["Service"]["ReadWritePaths"], "/var/lib/sarrafi")
        self.assertEqual(unit["Service"]["ProtectSystem"], "strict")

    def test_nginx_never_exposes_uploads_and_overwrites_proxy_header(self):
        text = (DEPLOY / "nginx.conf").read_text(encoding="utf-8")
        lines = [line.strip() for line in text.splitlines() if not line.strip().startswith("#")]
        self.assertIn("location ^~ /media/ { return 404; }", lines)
        self.assertIn("location ^~ /payment_receipts/ { return 404; }", lines)
        self.assertIn("proxy_set_header X-Forwarded-Proto $scheme;", lines)
        self.assertIn("proxy_pass http://127.0.0.1:8000;", lines)
        self.assertIn("proxy_cache off;", lines)
        self.assertFalse(any(line.startswith(("alias ", "root ")) for line in lines))
        self.assertEqual(text.count("{"), text.count("}"))

    def test_environment_template_keeps_secrets_empty_and_data_external(self):
        values = dict(
            line.split("=", 1)
            for line in (DEPLOY / "sarrafi.env.example").read_text(encoding="utf-8").splitlines()
            if line and not line.startswith("#")
        )
        self.assertEqual(values["SECRET_KEY"], "")
        self.assertEqual(values["DB_PASSWORD"], "")
        self.assertEqual(values["DEBUG"], "false")
        self.assertEqual(values["SARRAFI_DESKTOP"], "false")
        self.assertEqual(values["DB_ENGINE"], "postgresql")
        self.assertEqual(values["SARRAFI_DATA_DIR"], "/var/lib/sarrafi")
        self.assertEqual(values["SARRAFI_MEDIA_ROOT"], "/var/lib/sarrafi/media")
        self.assertEqual(values["CSRF_TRUSTED_ORIGINS"], "https://" + values["ALLOWED_HOSTS"])

    def test_installed_waitress_accepts_the_service_arguments(self):
        from waitress.adjustments import Adjustments

        unit = configparser.ConfigParser(interpolation=None, strict=False)
        unit.read(DEPLOY / "sarrafi.service", encoding="utf-8")
        kwargs, arguments = Adjustments.parse_args(shlex.split(unit["Service"]["ExecStart"])[1:])
        # waitress.runner removes these CLI-only switches before serving.
        self.assertFalse(kwargs.pop("help"))
        self.assertFalse(kwargs.pop("call"))
        adjustments = Adjustments(**kwargs)
        self.assertEqual(arguments, ["config.wsgi:application"])
        self.assertEqual(adjustments.trusted_proxy, "127.0.0.1")


if __name__ == "__main__":
    unittest.main()
