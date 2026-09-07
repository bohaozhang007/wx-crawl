import json
import logging
import tempfile
import unittest
from pathlib import Path

from src.integrations.wechrss_history import (
    WechRssHistoryProvider,
    WechRssRiskControlError,
)


ROOT = Path(__file__).resolve().parents[1]


class WechRssCredentialTest(unittest.TestCase):
    def test_risk_control_is_a_terminal_provider_error(self):
        class FakeRiskControlError(RuntimeError):
            pass

        class FakeAuthExpiredError(RuntimeError):
            pass

        provider = object.__new__(WechRssHistoryProvider)
        provider.RiskControlError = FakeRiskControlError
        provider.AuthExpiredError = FakeAuthExpiredError
        provider._client = lambda: object()

        with self.assertRaisesRegex(WechRssRiskControlError, "已停止自动请求"):
            provider._call(
                lambda _: (_ for _ in ()).throw(FakeRiskControlError("-2041"))
            )

    def test_saved_credentials_are_reused_without_requesting_qr(self):
        with tempfile.TemporaryDirectory() as temporary:
            credential_path = Path(temporary) / "wechrss.json"
            credential_path.write_text(
                json.dumps(
                    {
                        "vid": "123456",
                        "accessToken": "access",
                        "refreshToken": "refresh",
                        "deviceId": "device",
                        "profile": "eink-2.1.2",
                    }
                ),
                encoding="utf-8",
            )
            qr_calls = []
            provider = WechRssHistoryProvider(
                ROOT / "third_party" / "wechrss",
                credential_path,
                logger=logging.getLogger("test"),
                qr_path=Path(temporary) / "qr.png",
                on_qr=qr_calls.append,
                on_status=lambda *_: None,
            )

            provider.ensure_login()

            self.assertEqual(provider.credentials.vid, "123456")
            self.assertTrue(provider.credentials.can_refresh)
            self.assertEqual(qr_calls, [])


if __name__ == "__main__":
    unittest.main()
