"""Clef requests go through AI Gateway; Jev requests never carry the Cloudflare header. Offline."""
import os, sys, unittest
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE); sys.path.insert(0, os.path.join(os.path.dirname(HERE), "s5-prompts"))
import clef_run as R  # noqa: E402
import p_run as P  # noqa: E402


class Gateway(unittest.TestCase):
    def test_clef_run_client_carries_the_gateway_header(self):
        c = R.Client("t", "a", model=R.MODEL, gateway=R.GATEWAY)
        self.assertEqual(c.headers.get("cf-aig-gateway-id"), "default")

    def test_default_client_has_no_gateway_header(self):
        self.assertNotIn("cf-aig-gateway-id", R.Client("t", "a").headers)

    def test_p_run_clef_providers_use_the_gateway_and_jev_does_not(self):
        env = {"CF_AI_TOKEN": "t", "CF_ACCOUNT_ID": "a", "TYPESAFE_API_KEY": "k"}
        old = {k: os.environ.get(k) for k in env}
        os.environ.update(env)
        try:
            for prov in ("clef", "clef27"):
                c = P.make_client(prov, None, None)
                self.assertEqual(c.headers.get("cf-aig-gateway-id"), "default", prov)
            self.assertNotIn("cf-aig-gateway-id", P.make_client("jev", None, None).headers)
        finally:
            for k, v in old.items():
                os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)


if __name__ == "__main__":
    unittest.main()
