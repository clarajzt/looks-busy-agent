import unittest

from lba.links import LinkSigner

from .helpers import temp_db


class LinkTests(unittest.TestCase):
    def setUp(self):
        self.db = temp_db()
        self.signer = LinkSigner(b"k" * 32, self.db)

    def test_make_and_consume_once(self):
        token = self.signer.make("ou_abc", "oauth")
        self.assertEqual(self.signer.consume(token, "oauth"), "ou_abc")
        self.assertIsNone(self.signer.consume(token, "oauth"), "single use")

    def test_wrong_purpose_and_tamper(self):
        token = self.signer.make("ou_abc", "mail")
        self.assertIsNone(self.signer.consume(token, "oauth"))
        nonce, sig = token.rsplit(".", 1)
        self.assertIsNone(self.signer.consume(f"{nonce}.{sig[:-1]}x", "mail"))
        self.assertIsNone(self.signer.consume("garbage", "mail"))
        self.assertEqual(self.signer.consume(token, "mail"), "ou_abc")

    def test_expired(self):
        token = self.signer.make("ou_abc", "oauth", ttl_seconds=-1)
        self.assertIsNone(self.signer.consume(token, "oauth"))

    def test_other_key_cannot_forge(self):
        token = self.signer.make("ou_abc", "oauth")
        other = LinkSigner(b"z" * 32, self.db)
        self.assertIsNone(other.consume(token, "oauth"))


if __name__ == "__main__":
    unittest.main()
