import unittest

from .helpers import SecretBox, TEST_KEY


class CryptoTests(unittest.TestCase):
    def test_roundtrip(self):
        box = SecretBox(TEST_KEY)
        blob = box.encrypt({"password": "p@ss 密码"})
        self.assertNotIn(b"p@ss", blob)
        self.assertEqual(box.decrypt(blob), {"password": "p@ss 密码"})

    def test_wrong_key_fails(self):
        blob = SecretBox(TEST_KEY).encrypt({"a": 1})
        with self.assertRaises(ValueError):
            SecretBox(SecretBox.generate_key()).decrypt(blob)

    def test_rotation(self):
        old_key, new_key = TEST_KEY, SecretBox.generate_key()
        blob = SecretBox(old_key).encrypt({"refresh_token": "r"})
        box = SecretBox(new_key, old_key)
        self.assertEqual(box.decrypt(blob)["refresh_token"], "r")
        rotated = box.rotate(blob)
        self.assertEqual(SecretBox(new_key).decrypt(rotated)["refresh_token"], "r")
        with self.assertRaises(ValueError):
            SecretBox(old_key).decrypt(rotated)

    def test_derive_is_stable_and_purpose_bound(self):
        box = SecretBox(TEST_KEY)
        self.assertEqual(box.derive("links"), box.derive("links"))
        self.assertNotEqual(box.derive("links"), box.derive("other"))


if __name__ == "__main__":
    unittest.main()
