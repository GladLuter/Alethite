import json
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def manifest(*path):
    with open(os.path.join(ROOT, *path), encoding='utf-8') as f:
        return json.load(f)


class ManifestTest(unittest.TestCase):
    def test_plugin_and_marketplace_carry_the_new_name(self):
        plugin, market = manifest('plugin', '.claude-plugin', 'plugin.json'), manifest('.claude-plugin', 'marketplace.json')
        self.assertEqual((plugin['name'], plugin['version']), ('alethite', '0.3.0'))
        self.assertTrue(plugin['description'].startswith('Alethite'))
        self.assertEqual((market['name'], market['plugins'][0]['name']), ('alethite-local', 'alethite'))
        self.assertTrue(market['description'].startswith('Alethite'))


if __name__ == '__main__':
    unittest.main()
